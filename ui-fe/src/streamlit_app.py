"""Streamlit UI for Lightship MVP.

Professional web interface for dashcam video upload, processing, and results.
Updated for Rekognition-based pipeline with video classification.
"""
import streamlit as st
import time
import json
import os
from pathlib import Path
import zipfile
from io import BytesIO

from api_client import APIClient
from visualization import FrameVisualizer


@st.cache_data(ttl=30, show_spinner=False)
def _cached_health_check(_api_client) -> bool:
    try:
        resp = _api_client.session.get(f"{_api_client.base_url}/health", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


st.set_page_config(
    page_title="Lightship -- Dashcam Analysis",
    page_icon="🚗",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .lightship-brand { font-size: 1.6rem; font-weight: 800; color: #4fc3f7; letter-spacing: 0.04em; }
    .lightship-tagline { font-size: 0.9rem; color: #8b949e; margin-top: -0.4rem; margin-bottom: 0.5rem; }
    .metric-card { background-color: #f0f2f6; padding: 1rem; border-radius: 0.5rem; margin: 0.5rem 0; }
    .stProgress > div > div > div > div { background-color: #4fc3f7; }
    .job-card { border: 1px solid #30363d; border-radius: 0.5rem; padding: 0.9rem 1rem; margin-bottom: 0.6rem; }
</style>
""", unsafe_allow_html=True)

# ─── Session State ─────────────────────────────────────────────────────────────

if "api_client" not in st.session_state:
    st.session_state.api_client = APIClient()
if "page" not in st.session_state:
    st.session_state.page = "home"
if "jobs" not in st.session_state:
    st.session_state.jobs = []
if "history_jobs" not in st.session_state:
    st.session_state.history_jobs = []
if "selected_history_job_idx" not in st.session_state:
    st.session_state.selected_history_job_idx = None
if "max_snapshots" not in st.session_state:
    st.session_state.max_snapshots = 5
if "show_priorities" not in st.session_state:
    st.session_state.show_priorities = ["critical", "high", "medium", "low", "none"]
if "results" not in st.session_state:
    st.session_state.results = None
if "annotated_frames" not in st.session_state:
    st.session_state.annotated_frames = []
if "json_data" not in st.session_state:
    st.session_state.json_data = None


def main():
    render_nav()
    page = st.session_state.page
    if page == "home":
        render_home()
    elif page == "processing":
        render_processing()
    elif page == "results":
        render_results()
    elif page == "history":
        render_history()


# ─── Navigation ────────────────────────────────────────────────────────────────

def render_nav():
    col_brand, col_nav1, col_nav2, col_back = st.columns([4, 2, 2, 2])
    page = st.session_state.page

    with col_brand:
        st.markdown('<div class="lightship-brand">🚗 Lightship</div>', unsafe_allow_html=True)
        st.markdown('<div class="lightship-tagline">Dashcam Video Analysis Platform (Rekognition Pipeline)</div>', unsafe_allow_html=True)

    with col_nav1:
        btn = "primary" if page == "home" else "secondary"
        if st.button("▶ Run New Pipeline", use_container_width=True, type=btn):
            st.session_state.page = "home"
            st.rerun()

    with col_nav2:
        btn = "primary" if page == "history" else "secondary"
        if st.button("📋 Historical Runs", use_container_width=True, type=btn):
            st.session_state.page = "history"
            st.rerun()

    with col_back:
        if page != "home":
            if st.button("← Back", use_container_width=True):
                st.session_state.page = "home"
                st.rerun()

    st.divider()


# ─── Sidebar Config ────────────────────────────────────────────────────────────

def _render_sidebar_config():
    with st.sidebar:
        st.header("⚙️ Pipeline Configuration")

        st.subheader("Rekognition Pipeline")
        st.info("Using Amazon Rekognition + Bedrock Claude for detection, classification, and config generation.")

        st.subheader("Frame Selection")
        st.session_state.max_snapshots = st.slider(
            "Max Frames to Analyse", min_value=1, max_value=10,
            value=st.session_state.max_snapshots,
            help="Number of frames to extract and analyse per video",
        )

        st.divider()

        st.subheader("Display Settings")
        st.session_state.show_priorities = st.multiselect(
            "Filter by Priority",
            options=["critical", "high", "medium", "low", "none"],
            default=st.session_state.show_priorities,
        )

        st.divider()

        st.subheader("🔌 API Status")
        if _cached_health_check(st.session_state.api_client):
            st.success("✅ Connected")
        else:
            st.warning("⚠️ API Disconnected")


# ─── Page: Home ────────────────────────────────────────────────────────────────

def render_home():
    _render_sidebar_config()

    st.header("Upload Videos")
    st.caption("Upload dashcam videos for automated annotation and classification.")

    uploaded_files = st.file_uploader(
        "Choose dashcam videos",
        type=["mp4", "avi", "mov"],
        accept_multiple_files=True,
        help="Supported: MP4, AVI, MOV. Select multiple for batch.",
    )

    if uploaded_files:
        import pandas as pd
        info = [{
            "Filename": f.name,
            "Size (MB)": f"{f.size / 1024 / 1024:.2f}",
            "Type": f.type or "video/mp4",
        } for f in uploaded_files]
        st.dataframe(pd.DataFrame(info), hide_index=True, use_container_width=True)

        if len(uploaded_files) == 1:
            st.video(uploaded_files[0])
        else:
            with st.expander(f"Preview: {uploaded_files[0].name}"):
                st.video(uploaded_files[0])

        st.divider()

        col1, col2 = st.columns([3, 1])
        with col1:
            st.info(f"**{len(uploaded_files)} video(s) selected** — Rekognition + Bedrock pipeline")
        with col2:
            label = f"🚀 Process {len(uploaded_files)} Video{'s' if len(uploaded_files) > 1 else ''}"
            if st.button(label, type="primary", use_container_width=True):
                _submit_batch(uploaded_files)

    if st.session_state.jobs:
        show_batch_status()


def _submit_batch(uploaded_files):
    config = {
        "snapshot_strategy": "naive",
        "max_snapshots": st.session_state.max_snapshots,
        "cleanup_frames": False,
        "use_cv_labeler": True,
    }

    new_jobs = []
    errors = []

    progress = st.progress(0, text="Uploading videos...")
    for i, uf in enumerate(uploaded_files):
        progress.progress(i / len(uploaded_files), text=f"Uploading {uf.name}...")
        uf.seek(0)
        job_id = st.session_state.api_client.upload_video(uf, config)
        if job_id:
            new_jobs.append({
                "job_id": job_id, "filename": uf.name,
                "status": "QUEUED", "progress": 0.0,
                "message": "Queued", "results": None,
                "annotated_frames": [], "json_data": None,
            })
        else:
            errors.append(uf.name)

    progress.progress(1.0, text="All uploads submitted!")
    time.sleep(0.5)
    progress.empty()

    st.session_state.jobs.extend(new_jobs)

    if new_jobs:
        st.success(f"✅ {len(new_jobs)} video(s) submitted!")
        st.session_state.page = "processing"
    if errors:
        st.error(f"❌ Failed: {', '.join(errors)}")

    st.rerun()


# ─── Page: Processing ──────────────────────────────────────────────────────────

def render_processing():
    jobs = st.session_state.jobs
    if not jobs:
        st.info("No active jobs. Return to upload videos.")
        return

    completed = sum(1 for j in jobs if j["status"] == "COMPLETED")
    failed = sum(1 for j in jobs if j["status"] == "FAILED")
    active = [j for j in jobs if j["status"] not in ("COMPLETED", "FAILED")]

    with st.sidebar:
        st.markdown(f"**{len(jobs)} job(s)**")
        st.markdown(f"- ✅ {completed} completed")
        st.markdown(f"- ❌ {failed} failed")
        st.markdown(f"- ⚙️ {len(active)} active")

    if not active:
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

    show_batch_status()


def show_batch_status():
    st.divider()
    st.subheader(f"📋 Batch Status — {len(st.session_state.jobs)} job(s)")

    cols_hdr = st.columns([3, 2, 1, 3])
    cols_hdr[0].markdown("**Filename**")
    cols_hdr[1].markdown("**Status**")
    cols_hdr[2].markdown("**Progress**")
    cols_hdr[3].markdown("**Message**")

    for job in st.session_state.jobs:
        cols = st.columns([3, 2, 1, 3])
        _render_job_row(cols, job)

    active = [j for j in st.session_state.jobs if j["status"] not in ("COMPLETED", "FAILED")]
    if not active:
        completed = sum(1 for j in st.session_state.jobs if j["status"] == "COMPLETED")
        failed = sum(1 for j in st.session_state.jobs if j["status"] == "FAILED")
        if completed:
            st.success(f"🎉 {completed} completed, {failed} failed.")
        return

    time.sleep(1)
    any_updated = False

    for job in st.session_state.jobs:
        if job["status"] in ("COMPLETED", "FAILED"):
            continue
        resp = st.session_state.api_client.get_status(job["job_id"])
        if not resp:
            continue
        job["status"] = resp.get("status", "UNKNOWN").upper()
        job["progress"] = float(resp.get("progress", 0.0))
        job["message"] = resp.get("message", "")
        any_updated = True

        if job["status"] == "COMPLETED":
            results = st.session_state.api_client.get_results(job["job_id"])
            if results:
                job["results"] = results
                job["json_data"] = st.session_state.api_client.get_json_content(job["job_id"])
                st.session_state.results = results
                st.session_state.json_data = job["json_data"]

    if any_updated:
        st.rerun()


def _render_job_row(cols, job):
    icons = {"QUEUED": "⏳", "PROCESSING": "🔄", "COMPLETED": "✅", "FAILED": "❌"}
    cols[0].write(job["filename"])
    cols[1].write(f"{icons.get(job['status'], '❓')} {job['status']}")
    cols[2].write(f"{int(job.get('progress', 0) * 100)}%")
    cols[3].write(job.get("message", ""))


# ─── Page: Results ─────────────────────────────────────────────────────────────

def render_results():
    with st.sidebar:
        st.session_state.show_priorities = st.multiselect(
            "Filter by Priority",
            options=["critical", "high", "medium", "low", "none"],
            default=st.session_state.show_priorities,
        )

    completed = [j for j in st.session_state.jobs if j["status"] == "COMPLETED" and j.get("results")]

    if not completed and not st.session_state.results:
        st.info("Upload and process videos to see results.")
        return

    if len(completed) > 1:
        st.header(f"📊 Batch Results — {len(completed)} videos")
        tabs = st.tabs([f"🎥 {j['filename'][:30]}" for j in completed])
        for tab, job in zip(tabs, completed):
            with tab:
                _render_job_results(job.get("results"), job.get("json_data"))
    else:
        job = completed[0] if completed else None
        results = job["results"] if job else st.session_state.results
        json_data = job.get("json_data") if job else st.session_state.json_data
        _render_job_results(results, json_data)


def _render_job_results(results, json_data):
    if not results:
        st.info("No results available.")
        return

    summary = results.get("summary", {})

    st.header("📊 Summary")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Video Class", summary.get("video_class", "unknown"))
    c2.metric("Road Type", summary.get("road_type", "unknown"))
    c3.metric("Total Objects", summary.get("total_objects", 0))
    c4.metric("Hazard Events", summary.get("num_hazards", 0))

    st.divider()

    # Client config section
    client_config = None
    if json_data and isinstance(json_data, dict):
        client_config = json_data.get("client_config")

    if client_config:
        st.header("📋 Client Config Output")
        vc = client_config.get("video_class", "unknown")
        st.markdown(f"**Config Type:** `{vc}`")

        with st.expander("View Client Config JSON", expanded=False):
            st.json(client_config)

        st.download_button(
            "⬇️ Download Client Config",
            data=json.dumps(client_config, indent=2),
            file_name=f"{client_config.get('filename', 'config')}_config.json",
            mime="application/json",
            use_container_width=True,
        )
        st.divider()

    # Hazard Events
    if json_data and json_data.get("hazard_events"):
        st.header("⚠️ Hazard Events")
        sev_icons = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢", "None": "⚪"}
        for i, h in enumerate(json_data["hazard_events"], 1):
            sev = h["hazard_severity"]
            icon = sev_icons.get(sev, "⚠️")
            with st.expander(f"{icon} Hazard {i}: {h['hazard_type']} ({sev})", expanded=(i <= 2)):
                st.markdown(f"**Time:** {h['start_time_ms']:.0f}ms")
                st.markdown(f"**Description:** {h['hazard_description']}")
                st.markdown(f"**Road Conditions:** {h['road_conditions']}")
        st.divider()

    # Full JSON
    if json_data:
        st.header("💾 Downloads")
        st.download_button(
            "📄 Download Full Output JSON",
            data=json.dumps(json_data, indent=2),
            file_name="lightship_output.json",
            mime="application/json",
            use_container_width=True,
        )

        with st.expander("📋 Full Pipeline Output", expanded=False):
            st.json(json_data)


# ─── Page: History ─────────────────────────────────────────────────────────────

def _load_history():
    try:
        st.session_state.history_jobs = st.session_state.api_client.list_jobs(50) or []
        st.session_state.selected_history_job_idx = None
    except Exception:
        st.session_state.history_jobs = []


def render_history():
    if not st.session_state.history_jobs:
        _load_history()
    history = st.session_state.history_jobs

    if st.button("🔄 Refresh"):
        _load_history()
        st.rerun()

    if not history:
        st.info("No historical runs found.")
        return

    col_list, col_view = st.columns([2, 8])

    with col_list:
        st.markdown("**Past Runs**")
        st.divider()
        icons = {"COMPLETED": "✅", "FAILED": "❌", "PROCESSING": "⚙️", "QUEUED": "⏳"}
        for i, job in enumerate(history):
            created = job.get("created_at", "")[:16].replace("T", " ")
            fname = job.get("filename", job["job_id"][:12])
            status = job.get("status", "UNKNOWN")
            icon = icons.get(status, "❓")
            vc = job.get("video_class", "")
            label = f"{icon} {fname[:22]}\n{created}"
            if vc:
                label += f"\n[{vc}]"
            btn = "primary" if st.session_state.selected_history_job_idx == i else "secondary"
            if st.button(label, key=f"hist_{i}", use_container_width=True, type=btn):
                st.session_state.selected_history_job_idx = i
                st.rerun()

    with col_view:
        idx = st.session_state.selected_history_job_idx
        if idx is None:
            st.info("← Select a run from the list.")
            return

        job = history[idx]
        job_id = job["job_id"]
        st.subheader(job.get("filename", job_id))

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Status", job.get("status", "UNKNOWN"))
        m2.metric("Video Class", job.get("video_class", "—"))
        m3.metric("Road Type", job.get("road_type", "—"))
        m4.metric("Created", job.get("created_at", "")[:16].replace("T", " "))

        st.divider()

        if job.get("status") == "COMPLETED":
            json_data = st.session_state.api_client.get_json_content(job_id)
            if json_data:
                st.download_button(
                    "⬇️ Download Output JSON",
                    data=json.dumps(json_data, indent=2),
                    file_name=f"{job_id}_output.json",
                    mime="application/json",
                )
                with st.expander("📋 Full JSON", expanded=False):
                    st.json(json_data)
            else:
                st.warning("Results no longer in memory. Check S3.")
                s3_uri = job.get("s3_results_uri", "")
                if s3_uri:
                    st.code(s3_uri, language="text")
        elif job.get("status") == "FAILED":
            st.error(f"Failed: {job.get('error_message', 'Unknown')}")
        else:
            st.info(f"Status: {job.get('status', 'UNKNOWN')}")


if __name__ == "__main__":
    main()
