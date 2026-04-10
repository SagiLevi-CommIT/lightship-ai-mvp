"""Streamlit UI for Lightship MVP.

Professional web interface for video upload, processing, and results visualization.
"""
import streamlit as st
import requests
import time
import json
import os
from pathlib import Path
import tempfile
import zipfile
from io import BytesIO

from api_client import APIClient
from visualization import FrameVisualizer


@st.cache_data(ttl=30, show_spinner=False)
def _cached_health_check(_api_client) -> bool:
    """Non-blocking cached health check (30s TTL)."""
    try:
        resp = _api_client.session.get(f"{_api_client.base_url}/health", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False

# Page config
st.set_page_config(
    page_title="Lightship — Dashcam Analysis",
    page_icon="🚗",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .lightship-brand {
        font-size: 1.6rem;
        font-weight: 800;
        color: #4fc3f7;
        letter-spacing: 0.04em;
    }
    .lightship-tagline {
        font-size: 0.9rem;
        color: #8b949e;
        margin-top: -0.4rem;
        margin-bottom: 0.5rem;
    }
    .metric-card {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 0.5rem;
        margin: 0.5rem 0;
    }
    .stProgress > div > div > div > div {
        background-color: #4fc3f7;
    }
    .success-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #d4edda;
        border: 1px solid #c3e6cb;
        color: #155724;
    }
    .warning-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #fff3cd;
        border: 1px solid #ffeaa7;
        color: #856404;
    }
    .info-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #d1ecf1;
        border: 1px solid #bee5eb;
        color: #0c5460;
    }
    .job-card {
        border: 1px solid #30363d;
        border-radius: 0.5rem;
        padding: 0.9rem 1rem;
        margin-bottom: 0.6rem;
    }
</style>
""", unsafe_allow_html=True)

# Initialize session state
if 'api_client' not in st.session_state:
    st.session_state.api_client = APIClient()
# Page routing: 'home' | 'processing' | 'results' | 'history'
if 'page' not in st.session_state:
    st.session_state.page = 'home'
# Batch job tracking
if 'jobs' not in st.session_state:
    st.session_state.jobs = []
if 'show_completion_popup' not in st.session_state:
    st.session_state.show_completion_popup = False
# History page
if 'history_jobs' not in st.session_state:
    st.session_state.history_jobs = []
if 'selected_history_job_idx' not in st.session_state:
    st.session_state.selected_history_job_idx = None
if 'selected_results_file_idx' not in st.session_state:
    st.session_state.selected_results_file_idx = 0
# Pipeline config (persistent across pages via session_state)
if 'use_cv_labeler' not in st.session_state:
    st.session_state.use_cv_labeler = True
if 'snapshot_strategy' not in st.session_state:
    st.session_state.snapshot_strategy = 'naive'
if 'max_snapshots' not in st.session_state:
    st.session_state.max_snapshots = 3
if 'hazard_mode' not in st.session_state:
    st.session_state.hazard_mode = 'sliding_window'
if 'window_size' not in st.session_state:
    st.session_state.window_size = 3
if 'window_overlap' not in st.session_state:
    st.session_state.window_overlap = 1
if 'show_priorities' not in st.session_state:
    st.session_state.show_priorities = ['critical', 'high', 'medium', 'low', 'none']
if 'bbox_thickness' not in st.session_state:
    st.session_state.bbox_thickness = 2
if 'font_scale' not in st.session_state:
    st.session_state.font_scale = 0.6
if 'iou_threshold' not in st.session_state:
    st.session_state.iou_threshold = 0.3
if 'use_center_distance' not in st.session_state:
    st.session_state.use_center_distance = False
if 'center_distance_threshold' not in st.session_state:
    st.session_state.center_distance_threshold = 50
# Legacy single-job state (backward compat)
if 'results' not in st.session_state:
    st.session_state.results = None
if 'annotated_frames' not in st.session_state:
    st.session_state.annotated_frames = []
if 'json_data' not in st.session_state:
    st.session_state.json_data = None


def main():
    """Main Streamlit app — page-based routing."""
    render_nav()
    page = st.session_state.page
    if page == 'home':
        render_home()
    elif page == 'processing':
        render_processing()
    elif page == 'results':
        render_results()
    elif page == 'history':
        render_history()


# ─── Navigation ────────────────────────────────────────────────────────────────

def render_nav():
    """Branded top navigation bar."""
    col_brand, col_nav1, col_nav2, col_back = st.columns([4, 2, 2, 2])
    page = st.session_state.page

    with col_brand:
        st.markdown('<div class="lightship-brand">🚗 Lightship</div>', unsafe_allow_html=True)
        st.markdown('<div class="lightship-tagline">Dashcam Video Analysis Platform</div>', unsafe_allow_html=True)

    with col_nav1:
        btn_type = "primary" if page == 'home' else "secondary"
        if st.button("▶ Run New Pipeline", use_container_width=True, type=btn_type):
            st.session_state.page = 'home'
            st.rerun()

    with col_nav2:
        btn_type = "primary" if page == 'history' else "secondary"
        if st.button("📋 Historical Runs", use_container_width=True, type=btn_type):
            st.session_state.page = 'history'
            st.rerun()

    with col_back:
        if page != 'home':
            if st.button("← Back", use_container_width=True):
                st.session_state.page = 'home'
                st.rerun()

    st.divider()


# ─── Sidebar config wizard ─────────────────────────────────────────────────────

def _render_sidebar_config():
    """Render pipeline configuration wizard in the sidebar."""
    with st.sidebar:
        st.header("⚙️ Pipeline Configuration")

        st.subheader("Pipeline")
        st.session_state.use_cv_labeler = st.checkbox(
            "Use V2 Pipeline (CV + Temporal LLM)",
            value=st.session_state.use_cv_labeler,
            help="V2: CV Models + Hazard LLM | V1: LLM Image Analysis"
        )

        st.subheader("Frame Selection")
        st.session_state.snapshot_strategy = st.selectbox(
            "Strategy",
            options=["naive", "scene_change"],
            index=["naive", "scene_change"].index(st.session_state.snapshot_strategy),
            help="Naive: Uniform sampling | Scene Change: CV-based detection"
        )
        st.session_state.max_snapshots = st.slider(
            "Max Snapshots", min_value=1, max_value=10,
            value=st.session_state.max_snapshots,
            help="Number of frames to extract and analyze"
        )

        if st.session_state.use_cv_labeler:
            st.subheader("V2 Hazard Assessment")
            st.session_state.hazard_mode = st.selectbox(
                "LLM Mode",
                options=["sliding_window", "full_video"],
                index=["sliding_window", "full_video"].index(st.session_state.hazard_mode),
                help="Sliding window: 3-frame windows | Full video: entire sequence"
            )
            if st.session_state.hazard_mode == "sliding_window":
                st.session_state.window_size = st.slider(
                    "Window Size (frames)", min_value=2, max_value=5,
                    value=st.session_state.window_size
                )
                st.session_state.window_overlap = st.slider(
                    "Window Overlap (frames)", min_value=0,
                    max_value=max(1, st.session_state.window_size - 1),
                    value=min(st.session_state.window_overlap, st.session_state.window_size - 1)
                )

        st.divider()

        st.subheader("Display Settings")
        st.session_state.show_priorities = st.multiselect(
            "Filter by Priority",
            options=["critical", "high", "medium", "low", "none"],
            default=st.session_state.show_priorities,
            help="Select which threat levels to display"
        )
        st.session_state.bbox_thickness = st.slider(
            "Bounding Box Thickness", min_value=1, max_value=5,
            value=st.session_state.bbox_thickness
        )
        st.session_state.font_scale = st.slider(
            "Label Font Scale", min_value=0.3, max_value=1.5,
            value=st.session_state.font_scale, step=0.1
        )

        st.divider()

        st.subheader("Evaluation Settings")
        st.session_state.iou_threshold = st.slider(
            "IoU Threshold", min_value=0.1, max_value=0.7,
            value=st.session_state.iou_threshold, step=0.05,
            help="Intersection over Union threshold (lower = more lenient)"
        )
        st.session_state.use_center_distance = st.checkbox(
            "Use Center Distance Matching",
            value=st.session_state.use_center_distance
        )
        if st.session_state.use_center_distance:
            st.session_state.center_distance_threshold = st.slider(
                "Center Distance Threshold (px)", min_value=10, max_value=100,
                value=st.session_state.center_distance_threshold, step=10
            )

        st.divider()

        st.subheader("🔌 API Status")
        if _cached_health_check(st.session_state.api_client):
            st.success("✅ Connected")
        else:
            st.warning("⚠️ API Disconnected")


def _get_v2_config():
    """Build V2 config dict from session state, or None if V1."""
    if not st.session_state.use_cv_labeler:
        return None
    return {
        'hazard_mode': st.session_state.hazard_mode,
        'window_size': st.session_state.window_size,
        'window_overlap': st.session_state.window_overlap,
    }


# ─── Page: Home ────────────────────────────────────────────────────────────────

def render_home():
    """Main pipeline workspace — batch upload + evaluation."""
    _render_sidebar_config()

    tab1, tab2 = st.tabs(["📁 Batch Mode", "📊 Evaluation"])

    with tab1:
        upload_and_process_tab(
            st.session_state.snapshot_strategy,
            st.session_state.max_snapshots,
            st.session_state.use_cv_labeler,
            _get_v2_config()
        )

    with tab2:
        evaluation_tab(
            st.session_state.iou_threshold,
            st.session_state.use_center_distance,
            st.session_state.center_distance_threshold
        )


# ─── Page: Processing ──────────────────────────────────────────────────────────

def render_processing():
    """Processing screen — shows per-job status and completion popup."""
    jobs = st.session_state.jobs
    if not jobs:
        st.info("No active jobs found. Return to upload videos.")
        return

    completed = sum(1 for j in jobs if j["status"] == "COMPLETED")
    failed = sum(1 for j in jobs if j["status"] == "FAILED")
    active = [j for j in jobs if j["status"] not in ("COMPLETED", "FAILED")]

    with st.sidebar:
        st.markdown(f"**{len(jobs)} job(s) submitted**")
        st.markdown(f"- ✅ {completed} completed")
        st.markdown(f"- ❌ {failed} failed")
        st.markdown(f"- ⚙️ {len(active)} active")

    if not active:
        # All done — completion popup
        if completed:
            st.success(f"✅ Batch complete — {completed} succeeded, {failed} failed.")
        else:
            st.error(f"❌ All {failed} job(s) failed.")

        col1, col2 = st.columns(2)
        with col1:
            if completed and st.button("📊 View Results", type="primary", use_container_width=True):
                st.session_state.page = "results"
                st.rerun()
        with col2:
            if st.button("🔄 Run New Pipeline", use_container_width=True):
                st.session_state.jobs = []
                st.session_state.page = "home"
                st.rerun()
        st.divider()

    # Show batch status cards
    show_batch_status(
        st.session_state.snapshot_strategy,
        st.session_state.max_snapshots,
        st.session_state.use_cv_labeler,
        _get_v2_config()
    )


# ─── Page: Results ─────────────────────────────────────────────────────────────

def render_results():
    """Results page — file selector + annotated frames + properties."""
    with st.sidebar:
        st.markdown("**Display Settings**")
        st.session_state.show_priorities = st.multiselect(
            "Filter by Priority",
            options=["critical", "high", "medium", "low", "none"],
            default=st.session_state.show_priorities
        )
    results_tab(
        st.session_state.show_priorities,
        st.session_state.bbox_thickness,
        st.session_state.font_scale
    )


# ─── Page: History ─────────────────────────────────────────────────────────────

def _load_history():
    """Fetch job history from backend, store in session_state."""
    try:
        jobs = st.session_state.api_client.list_jobs(limit=50)
        st.session_state.history_jobs = jobs or []
        st.session_state.selected_history_job_idx = None
    except Exception:
        st.session_state.history_jobs = []


def render_history():
    """Historical pipeline runs page."""
    # Lazy-load on first visit
    if not st.session_state.history_jobs:
        _load_history()
    history = st.session_state.history_jobs

    col_refresh, _ = st.columns([1, 9])
    with col_refresh:
        if st.button("🔄 Refresh"):
            _load_history()
            st.rerun()

    if not history:
        st.info("No historical runs found. Run a pipeline to see results here.")
        return

    col_list, col_view = st.columns([2, 8])

    with col_list:
        st.markdown("**Past Runs**")
        st.divider()
        status_icons = {"COMPLETED": "✅", "FAILED": "❌", "PROCESSING": "⚙️", "QUEUED": "⏳"}
        for i, job in enumerate(history):
            created = job.get("created_at", "")[:16].replace("T", " ")
            fname = job.get("filename", job["job_id"][:12])
            status = job.get("status", "UNKNOWN")
            icon = status_icons.get(status, "❓")
            label = f"{icon} {fname[:22]}\n{created}"
            btn_type = "primary" if st.session_state.selected_history_job_idx == i else "secondary"
            if st.button(label, key=f"hist_{i}", use_container_width=True, type=btn_type):
                st.session_state.selected_history_job_idx = i
                st.rerun()

    with col_view:
        idx = st.session_state.selected_history_job_idx
        if idx is None:
            st.info("← Select a run from the list to view its details.")
            return

        job = history[idx]
        job_id = job["job_id"]

        st.subheader(job.get("filename", job_id))

        m1, m2, m3 = st.columns(3)
        m1.metric("Status", job.get("status", "UNKNOWN"))
        m2.metric("Job ID", job_id[:16] + "…")
        m3.metric("Created", job.get("created_at", "")[:16].replace("T", " "))

        st.divider()

        if job.get("status") == "COMPLETED":
            json_data = st.session_state.api_client.get_json_content(job_id)
            if json_data:
                st.download_button(
                    "⬇️ Download All JSON Files",
                    data=json.dumps(json_data, indent=2),
                    file_name=f"{job_id}_output.json",
                    mime="application/json"
                )
                summary = json_data.get("summary", {})
                if summary:
                    s1, s2, s3 = st.columns(3)
                    s1.metric("Total Objects", summary.get("total_objects", 0))
                    s2.metric("Snapshots", summary.get("num_snapshots", 0))
                    s3.metric("Hazard Events", summary.get("num_hazards", 0))
                with st.expander("📋 Full JSON Output", expanded=False):
                    st.json(json_data)
            else:
                st.warning("⚠️ In-memory results are no longer available (Lambda may have restarted).")
                st.info("Download JSON immediately after processing completes to preserve results.")
        elif job.get("status") == "FAILED":
            st.error(f"Job failed: {job.get('error_message', 'Unknown error')}")
        else:
            st.info(f"Job status: {job.get('status', 'UNKNOWN')}")



def upload_and_process_tab(snapshot_strategy, max_snapshots, use_cv_labeler, v2_config):
    """Upload and process video tab — supports single or batch uploads."""

    st.header("Upload Videos")
    st.caption("Upload one or multiple dashcam videos. Each video is processed independently.")

    uploaded_files = st.file_uploader(
        "Choose dashcam videos",
        type=['mp4', 'avi', 'mov'],
        accept_multiple_files=True,
        help="Supported formats: MP4, AVI, MOV. Select multiple files for batch processing."
    )

    if uploaded_files:
        # Show batch summary table
        import pandas as pd
        file_info = [{
            "Filename": f.name,
            "Size (MB)": f"{f.size / 1024 / 1024:.2f}",
            "Type": f.type or "video/mp4"
        } for f in uploaded_files]
        st.dataframe(pd.DataFrame(file_info), hide_index=True, use_container_width=True)

        # Preview first video
        if len(uploaded_files) == 1:
            st.video(uploaded_files[0])
        else:
            with st.expander(f"Preview first video: {uploaded_files[0].name}"):
                st.video(uploaded_files[0])

        st.divider()

        col1, col2 = st.columns([3, 1])
        with col1:
            st.info(f"**{len(uploaded_files)} video(s) selected** — Pipeline: {'V2 (CV + Temporal LLM)' if use_cv_labeler else 'V1 (LLM)'}")
        with col2:
            label = f"🚀 Process {len(uploaded_files)} Video{'s' if len(uploaded_files) > 1 else ''}"
            if st.button(label, type="primary", use_container_width=True):
                _submit_batch(uploaded_files, snapshot_strategy, max_snapshots, use_cv_labeler, v2_config)

    # Show batch status for active and recent jobs
    if st.session_state.jobs:
        show_batch_status(snapshot_strategy, max_snapshots, use_cv_labeler, v2_config)


def _build_config(snapshot_strategy, max_snapshots, use_cv_labeler, v2_config):
    """Build processing config dict."""
    config = {
        "snapshot_strategy": snapshot_strategy,
        "max_snapshots": max_snapshots,
        "cleanup_frames": False,
        "use_cv_labeler": use_cv_labeler
    }
    if v2_config:
        config.update(v2_config)
    return config


def _submit_batch(uploaded_files, snapshot_strategy, max_snapshots, use_cv_labeler, v2_config):
    """Submit all uploaded files for processing, one job per file."""
    config = _build_config(snapshot_strategy, max_snapshots, use_cv_labeler, v2_config)

    new_jobs = []
    errors = []

    progress = st.progress(0, text="Uploading videos...")
    for i, uploaded_file in enumerate(uploaded_files):
        progress.progress((i) / len(uploaded_files), text=f"Uploading {uploaded_file.name}...")
        uploaded_file.seek(0)
        job_id = st.session_state.api_client.upload_video(uploaded_file, config)
        if job_id:
            new_jobs.append({
                "job_id": job_id,
                "filename": uploaded_file.name,
                "status": "QUEUED",
                "progress": 0.0,
                "message": "Queued",
                "results": None,
                "annotated_frames": [],
                "json_data": None,
            })
        else:
            errors.append(uploaded_file.name)

    progress.progress(1.0, text="All uploads submitted!")
    time.sleep(0.5)
    progress.empty()

    st.session_state.jobs.extend(new_jobs)

    if new_jobs:
        st.success(f"✅ {len(new_jobs)} video(s) submitted for processing!")
        st.session_state.page = 'processing'
    if errors:
        st.error(f"❌ Failed to upload: {', '.join(errors)}")

    st.rerun()


def show_batch_status(snapshot_strategy, max_snapshots, use_cv_labeler, v2_config):
    """Display real-time status for all jobs in the batch."""

    st.divider()
    st.subheader(f"📋 Batch Status — {len(st.session_state.jobs)} job(s)")

    # Column headers
    col_hdr = st.columns([3, 2, 1, 3])
    col_hdr[0].markdown("**Filename**")
    col_hdr[1].markdown("**Status**")
    col_hdr[2].markdown("**Progress**")
    col_hdr[3].markdown("**Message**")

    # One row per job
    row_placeholders = []
    for job in st.session_state.jobs:
        cols = st.columns([3, 2, 1, 3])
        row_placeholders.append(cols)
        _render_job_row(cols, job)

    # Check if any jobs are still active
    active_jobs = [j for j in st.session_state.jobs if j["status"] not in ("COMPLETED", "FAILED")]

    if not active_jobs:
        completed = sum(1 for j in st.session_state.jobs if j["status"] == "COMPLETED")
        failed = sum(1 for j in st.session_state.jobs if j["status"] == "FAILED")
        if completed:
            st.success(f"🎉 {completed} job(s) completed successfully, {failed} failed.")
        else:
            st.error(f"❌ All {failed} job(s) failed.")
        return

    # Poll active jobs once per second
    time.sleep(1)
    any_updated = False

    for job in st.session_state.jobs:
        if job["status"] in ("COMPLETED", "FAILED"):
            continue

        status_resp = st.session_state.api_client.get_status(job["job_id"])
        if not status_resp:
            continue

        raw_status = status_resp.get("status", "UNKNOWN").upper()
        job["status"] = raw_status
        job["progress"] = float(status_resp.get("progress", 0.0))
        job["message"] = status_resp.get("message", "")
        any_updated = True

        if raw_status == "COMPLETED":
            results = st.session_state.api_client.get_results(job["job_id"])
            if results:
                job["results"] = results
                job["annotated_frames"] = _generate_annotated_frames_for_job(job["job_id"], results)
                # Keep legacy session state pointing to last completed job for results tab
                st.session_state.results = results
                st.session_state.annotated_frames = job["annotated_frames"]
                st.session_state.json_data = job["json_data"]

    if any_updated:
        st.rerun()


def _render_job_row(cols, job):
    """Render a single job status row."""
    status = job["status"]
    status_icons = {
        "QUEUED": "⏳ QUEUED",
        "PROCESSING": "🔄 PROCESSING",
        "COMPLETED": "✅ COMPLETED",
        "FAILED": "❌ FAILED",
    }
    cols[0].write(job["filename"])
    cols[1].write(status_icons.get(status, status))
    cols[2].write(f"{int(job.get('progress', 0) * 100)}%")
    cols[3].write(job.get("message", ""))


def _generate_annotated_frames_for_job(job_id, results):
    """Generate annotated frames for a job. Returns list of frame dicts."""
    visualizer = FrameVisualizer()
    annotated_frames = []

    json_data = st.session_state.api_client.get_json_content(job_id)

    # Store on matching job entry
    for job in st.session_state.jobs:
        if job["job_id"] == job_id:
            job["json_data"] = json_data
            break

    if json_data and results.get('snapshots'):
        for snapshot in results['snapshots']:
            frame_path = snapshot['frame_path']
            timestamp = snapshot['timestamp_ms']

            objects_at_timestamp = [
                obj for obj in json_data.get('objects', [])
                if abs(obj['start_time_ms'] - timestamp) < 1.0
            ]

            try:
                annotated_img, annotated_path = visualizer.annotate_frame(
                    frame_path, objects_at_timestamp, timestamp
                )
            except Exception:
                annotated_img, annotated_path = None, frame_path

            annotated_frames.append({
                'timestamp': timestamp,
                'frame_idx': snapshot['frame_idx'],
                'original_path': frame_path,
                'annotated_path': annotated_path,
                'annotated_image': annotated_img,
                'objects': objects_at_timestamp
            })

    return annotated_frames


def results_tab(show_priorities, bbox_thickness, font_scale):
    """Display processing results — handles single and batch results."""

    completed_jobs = [
        j for j in st.session_state.jobs
        if j["status"] == "COMPLETED" and j.get("results")
    ]

    if not completed_jobs and not st.session_state.results:
        st.info("👆 Upload and process video(s) to see results here.")
        return

    if len(completed_jobs) > 1:
        st.header(f"📊 Batch Results — {len(completed_jobs)} videos")
        tab_labels = [f"🎥 {j['filename'][:30]}" for j in completed_jobs]
        tabs = st.tabs(tab_labels)
        for tab, job in zip(tabs, completed_jobs):
            with tab:
                _render_single_job_results(
                    job["results"],
                    job.get("annotated_frames", []),
                    job.get("json_data"),
                    show_priorities
                )
    else:
        # Single video (from batch list or legacy session state)
        if completed_jobs:
            job = completed_jobs[0]
            results = job["results"]
            annotated_frames = job.get("annotated_frames", [])
            json_data = job.get("json_data")
        else:
            results = st.session_state.results
            annotated_frames = st.session_state.annotated_frames
            json_data = st.session_state.get("json_data")
        _render_single_job_results(results, annotated_frames, json_data, show_priorities)


def _render_single_job_results(results, annotated_frames, json_data, show_priorities):
    """Render results for a single job."""

    # Summary metrics
    st.header("📊 Summary")

    summary = results.get('summary', {})

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Objects", summary.get('total_objects', 0))

    with col2:
        st.metric("Snapshots Analyzed", summary.get('num_snapshots', 0))

    with col3:
        priority_count = sum(
            count for level, count in summary.get('priority_distribution', {}).items()
            if level in ['high', 'critical']
        )
        st.metric("Priority Hazards", priority_count)

    with col4:
        st.metric("Hazard Events", summary.get('num_hazards', 0))

    st.divider()

    # Hazard Events Section
    if json_data and json_data.get('hazard_events'):
        st.header("⚠️ Hazard Events")
        severity_icons = {'Critical': '🔴', 'High': '🟠', 'Medium': '🟡', 'Low': '🟢', 'None': '⚪'}
        for i, hazard in enumerate(json_data['hazard_events'], 1):
            severity = hazard['hazard_severity']
            icon = severity_icons.get(severity, '⚠️')
            with st.expander(f"{icon} Hazard {i}: {hazard['hazard_type']} (Severity: {severity})", expanded=(i <= 2)):
                st.markdown(f"**Time:** {hazard['start_time_ms']:.0f}ms")
                st.markdown(f"**Severity:** {severity}")
                st.markdown(f"**Type:** {hazard['hazard_type']}")
                st.markdown(f"**Description:** {hazard['hazard_description']}")
                st.markdown(f"**Road Conditions:** {hazard['road_conditions']}")
        st.divider()

    # Priority distribution
    priority_dist = summary.get('priority_distribution', {})
    if priority_dist:
        st.subheader("Priority Distribution")
        col1, col2 = st.columns([2, 1])
        with col1:
            import plotly.graph_objects as go
            colors = {'critical': '#d62728', 'high': '#ff7f0e', 'medium': '#ffbb78', 'low': '#98df8a', 'none': '#c7c7c7'}
            fig = go.Figure(data=[go.Bar(
                x=list(priority_dist.keys()),
                y=list(priority_dist.values()),
                marker_color=[colors.get(k, '#1f77b4') for k in priority_dist.keys()]
            )])
            fig.update_layout(title="Objects by Priority", xaxis_title="Priority", yaxis_title="Count", height=300)
            st.plotly_chart(fig, use_container_width=True)
        with col2:
            st.markdown("**Priority Levels:**")
            for level, count in sorted(priority_dist.items(), key=lambda x: x[1], reverse=True):
                st.write(f"- **{level.upper()}**: {count}")
        st.divider()

    # Annotated frames
    st.header("🖼️ Annotated Frames")
    if annotated_frames:
        for i, frame_data in enumerate(annotated_frames):
            with st.expander(
                f"Frame {i+1} @ {frame_data['timestamp']:.2f}ms — {len(frame_data['objects'])} objects",
                expanded=(i == 0)
            ):
                if frame_data.get('annotated_image') is not None:
                    st.image(
                        frame_data['annotated_image'],
                        caption=f"Frame {frame_data['frame_idx']} at {frame_data['timestamp']:.2f}ms",
                        use_container_width=True
                    )
                filtered_objects = [
                    obj for obj in frame_data['objects'] if obj['priority'] in show_priorities
                ]
                if filtered_objects:
                    import pandas as pd
                    df = pd.DataFrame([{
                        'Description': obj['description'],
                        'Priority': obj['priority'].upper(),
                        'Distance': obj['distance'],
                        'Center': f"({obj['center']['x']}, {obj['center']['y']})"
                    } for obj in filtered_objects])
                    st.dataframe(df, use_container_width=True, hide_index=True)
                else:
                    st.info("No objects match the selected priority filters.")
    else:
        st.info("No annotated frames available.")

    st.divider()

    # Downloads
    st.header("💾 Downloads")
    col1, col2, col3 = st.columns(3)
    with col1:
        if json_data:
            st.download_button(
                label="📄 Download JSON",
                data=json.dumps(json_data, indent=2),
                file_name="lightship_output.json",
                mime="application/json",
                use_container_width=True
            )
    with col2:
        zip_data = create_annotated_frames_zip(annotated_frames)
        if zip_data:
            st.download_button(
                label="🖼️ Download Annotated Frames (ZIP)",
                data=zip_data,
                file_name="annotated_frames.zip",
                mime="application/zip",
                use_container_width=True
            )
    with col3:
        zip_data = create_complete_zip(annotated_frames, json_data)
        if zip_data:
            st.download_button(
                label="📦 Download All Files (ZIP)",
                data=zip_data,
                file_name="lightship_complete_output.zip",
                mime="application/zip",
                use_container_width=True
            )


def create_annotated_frames_zip(annotated_frames=None):
    """Create ZIP file with annotated frames."""
    frames = annotated_frames if annotated_frames is not None else st.session_state.annotated_frames
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for frame_data in frames:
            if frame_data.get('annotated_path') and os.path.exists(frame_data['annotated_path']):
                zip_file.write(
                    frame_data['annotated_path'],
                    arcname=f"frame_{frame_data['frame_idx']}_annotated.png"
                )
    zip_buffer.seek(0)
    return zip_buffer.getvalue()


def create_complete_zip(annotated_frames=None, json_data=None):
    """Create ZIP file with all outputs."""
    frames = annotated_frames if annotated_frames is not None else st.session_state.annotated_frames
    json_content = json_data if json_data is not None else st.session_state.get('json_data')
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        if json_content:
            zip_file.writestr("output.json", json.dumps(json_content, indent=2))

        # Add annotated frames
        for frame_data in frames:
            if frame_data.get('annotated_path') and os.path.exists(frame_data['annotated_path']):
                zip_file.write(
                    frame_data['annotated_path'],
                    arcname=f"annotated/frame_{frame_data['frame_idx']}.png"
                )

            # Add original frames
            if frame_data.get('original_path') and os.path.exists(frame_data['original_path']):
                zip_file.write(
                    frame_data['original_path'],
                    arcname=f"original/frame_{frame_data['frame_idx']}.png"
                )

    zip_buffer.seek(0)
    return zip_buffer.getvalue()


def evaluation_tab(iou_threshold, use_center_distance, center_distance_threshold):
    """Evaluation metrics tab."""

    st.header("📈 Evaluation Metrics")

    st.info("""
    **Note:** Evaluation requires ground truth data in `data/train/` directory.
    This tab compares generated outputs with ground truth annotations.
    """)

    # Configuration display
    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("IoU Threshold", f"{iou_threshold:.2f}")

    with col2:
        st.metric("Center Distance", "Enabled" if use_center_distance else "Disabled")

    with col3:
        if use_center_distance:
            st.metric("Distance Threshold", f"{center_distance_threshold}px")
        else:
            st.metric("Distance Threshold", "N/A")

    st.divider()

    # Run evaluation button
    if st.button("🔬 Run Evaluation", type="primary", width="stretch"):
        run_evaluation(iou_threshold, use_center_distance, center_distance_threshold)

    # Display cached results if available
    if 'evaluation_results' in st.session_state:
        display_evaluation_results(st.session_state.evaluation_results)


def run_evaluation(iou_threshold, use_center_distance, center_distance_threshold):
    """Run evaluation with current settings."""
    import sys
    import os

    # Add parent directory to path to import src modules
    parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    with st.spinner("Running evaluation..."):
        try:
            # Import evaluation module
            from src.evaluation_flexible import calculate_metrics_flexible

            # Change to project root directory for evaluation
            original_dir = os.getcwd()
            os.chdir(parent_dir)

            # Redirect stdout to capture results
            from io import StringIO
            old_stdout = sys.stdout
            sys.stdout = captured_output = StringIO()

            # Run evaluation
            calculate_metrics_flexible(
                iou_threshold=iou_threshold,
                use_center_distance=use_center_distance,
                center_distance_threshold=center_distance_threshold,
                silent=False
            )

            # Get output
            output = captured_output.getvalue()
            sys.stdout = old_stdout

            # Restore directory
            os.chdir(original_dir)

            # Parse output (simplified - just extract key metrics)
            st.session_state.evaluation_results = {
                'output': output,
                'iou_threshold': iou_threshold,
                'use_center_distance': use_center_distance,
                'center_distance_threshold': center_distance_threshold
            }

            st.success("✅ Evaluation completed!")
            st.rerun()

        except Exception as e:
            sys.stdout = old_stdout
            if 'original_dir' in locals():
                os.chdir(original_dir)

            st.error(f"❌ Evaluation failed: {str(e)}")
            st.info("💡 Make sure you have processed train videos and ground truth data is available.")

            # Show detailed error in expander
            with st.expander("🔍 Error Details"):
                import traceback
                st.code(traceback.format_exc())


def display_evaluation_results(results):
    """Display evaluation results."""

    st.success("✅ Evaluation Results")

    # Configuration used
    st.markdown("**Configuration:**")
    st.write(f"- IoU Threshold: {results['iou_threshold']:.2f}")
    st.write(f"- Center Distance: {'Enabled' if results['use_center_distance'] else 'Disabled'}")
    if results['use_center_distance']:
        st.write(f"- Distance Threshold: {results['center_distance_threshold']}px")

    st.divider()

    # Display raw output (formatted)
    with st.expander("📊 Detailed Metrics", expanded=True):
        st.code(results['output'], language='text')

    st.info("💡 **Tip:** Adjust IoU threshold and center distance settings in the sidebar to see how metrics change.")


def about_tab():
    """About section."""

    st.header("About Lightship MVP")

    st.markdown("""
    ### 🎯 Purpose

    This system performs **snapshot-based object detection and priority hazard identification**
    for dashcam videos using CV models and AWS Bedrock Claude Sonnet 4.

    ### 🔧 Features

    **V2 Pipeline (CV + Temporal LLM)**:
    - **Computer Vision Models**: YOLO11 for object detection, Depth-Anything-V2 for depth estimation
    - **Road Geometry Detection**: Lane markings, crosswalks, and double yellow lines
    - **Temporal Hazard Assessment**: LLM analyzes object sequences over time
    - **Hazard Events**: Contextual hazard identification with severity ratings
    - **Derived Threat Levels**: Object-level threats derived from hazard severity

    **V1 Pipeline (LLM Image Analysis)**:
    - **Image-based Detection**: Claude Sonnet 4 analyzes each frame directly
    - **Direct Threat Assignment**: LLM assigns threat levels per object

    **Common Features**:
    - **Intelligent Snapshot Selection**: Naive uniform sampling or CV-based scene change detection
    - **Distance Estimation**: Categorizes object proximity (dangerously_close, very_close, close, moderate, far, very_far)
    - **Visual Annotations**: Draws bounding boxes with labels on detected objects

    ### 📊 Output Format

    - **JSON**: Structured data with objects, hazard events, and metadata
    - **Annotated Frames**: Visual representation of detections with bounding boxes
    - **Summary Statistics**: Threat level and hazard severity distributions

    ### 🏗️ Architecture

    - **Backend**: FastAPI server wrapping the Lightship pipeline
    - **Frontend**: Streamlit web interface for user interaction
    - **V2 Processing**: YOLO11 + Depth-Anything-V2 + Temporal LLM (Claude Sonnet 4)
    - **V1 Processing**: AWS Bedrock (Claude Sonnet 4) for image analysis
    - **Visualization**: OpenCV for bounding box rendering

    ### 📚 Resources

    - [V2 Implementation Summary](../V2_IMPLEMENTATION_SUMMARY.md)
    - [System Specification](../references/Lightship_MVP_System_Spec_V2_CV_Labeler_Temporal_LLM.md)
    - [Documentation](../README.md)

    ---

    **Version:** 2.0.0 (V2 CV Pipeline)
    **Status:** Production-Ready MVP
    """)


if __name__ == "__main__":
    main()

