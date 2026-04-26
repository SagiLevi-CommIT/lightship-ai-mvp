"""Streamlit UI for Lightship MVP.

Professional web interface for dashcam video upload, processing, and results.
Displays selected frames, annotated frames, and structured detection results.
"""
import streamlit as st
import time
import json
import os
import zipfile
from io import BytesIO

from api_client import APIClient


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
    .stProgress > div > div > div > div { background-color: #4fc3f7; }
</style>
""", unsafe_allow_html=True)

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
        st.markdown('<div class="lightship-tagline">Dashcam Video Analysis Platform</div>', unsafe_allow_html=True)
    with col_nav1:
        if st.button("▶ New Pipeline", use_container_width=True,
                     type="primary" if page == "home" else "secondary"):
            st.session_state.page = "home"
            st.rerun()
    with col_nav2:
        if st.button("📋 History", use_container_width=True,
                     type="primary" if page == "history" else "secondary"):
            st.session_state.page = "history"
            st.rerun()
    with col_back:
        if page != "home":
            if st.button("← Back", use_container_width=True):
                st.session_state.page = "home"
                st.rerun()
    st.divider()


def _render_sidebar_config():
    with st.sidebar:
        st.header("⚙️ Pipeline Configuration")
        st.info("Amazon Rekognition + Bedrock Claude")
        st.session_state.max_snapshots = st.slider(
            "Max Frames to Analyse", min_value=1, max_value=10,
            value=st.session_state.max_snapshots)
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
        "Choose dashcam videos", type=["mp4", "avi", "mov"],
        accept_multiple_files=True, help="Supported: MP4, AVI, MOV.")

    if uploaded_files:
        import pandas as pd
        info = [{"Filename": f.name, "Size (MB)": f"{f.size / 1024 / 1024:.2f}",
                 "Type": f.type or "video/mp4"} for f in uploaded_files]
        st.dataframe(pd.DataFrame(info), hide_index=True, use_container_width=True)

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
    config = {"snapshot_strategy": "naive", "max_snapshots": st.session_state.max_snapshots,
              "cleanup_frames": False, "use_cv_labeler": True}
    new_jobs, errors = [], []
    progress = st.progress(0, text="Uploading videos...")
    for i, uf in enumerate(uploaded_files):
        progress.progress(i / len(uploaded_files), text=f"Uploading {uf.name}...")
        uf.seek(0)
        job_id = st.session_state.api_client.upload_video(uf, config)
        if job_id:
            new_jobs.append({"job_id": job_id, "filename": uf.name, "status": "QUEUED",
                             "progress": 0.0, "message": "Queued", "results": None, "json_data": None})
        else:
            errors.append(uf.name)
    progress.progress(1.0, text="Done!")
    time.sleep(0.3)
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
        st.info("No active jobs.")
        return
    completed = sum(1 for j in jobs if j["status"] == "COMPLETED")
    failed = sum(1 for j in jobs if j["status"] == "FAILED")
    active = [j for j in jobs if j["status"] not in ("COMPLETED", "FAILED")]
    with st.sidebar:
        st.markdown(f"**{len(jobs)} job(s)** — ✅{completed} ❌{failed} ⚙️{len(active)}")
    if not active:
        if completed:
            st.success(f"✅ Batch complete — {completed} succeeded, {failed} failed.")
        else:
            st.error(f"❌ All {failed} job(s) failed.")
        c1, c2 = st.columns(2)
        with c1:
            if completed and st.button("📊 View Results", type="primary", use_container_width=True):
                st.session_state.page = "results"
                st.rerun()
        with c2:
            if st.button("🔄 New Pipeline", use_container_width=True):
                st.session_state.jobs = []
                st.session_state.page = "home"
                st.rerun()
        st.divider()
    show_batch_status()


def show_batch_status():
    st.subheader(f"📋 Batch Status — {len(st.session_state.jobs)} job(s)")
    for job in st.session_state.jobs:
        icons = {"QUEUED": "⏳", "PROCESSING": "🔄", "COMPLETED": "✅", "FAILED": "❌"}
        cols = st.columns([3, 2, 1, 3])
        cols[0].write(job["filename"])
        cols[1].write(f"{icons.get(job['status'], '❓')} {job['status']}")
        cols[2].write(f"{int(job.get('progress', 0) * 100)}%")
        cols[3].write(job.get("message", ""))

    active = [j for j in st.session_state.jobs if j["status"] not in ("COMPLETED", "FAILED")]
    if not active:
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
            job["results"] = st.session_state.api_client.get_results(job["job_id"])
            job["json_data"] = st.session_state.api_client.get_json_content(job["job_id"])
    if any_updated:
        st.rerun()


# ─── Page: Results ─────────────────────────────────────────────────────────────

def render_results():
    with st.sidebar:
        st.session_state.show_priorities = st.multiselect(
            "Filter by Priority",
            options=["critical", "high", "medium", "low", "none"],
            default=st.session_state.show_priorities)

    completed = [j for j in st.session_state.jobs if j["status"] == "COMPLETED" and j.get("results")]
    if not completed:
        st.info("Upload and process videos to see results.")
        return

    if len(completed) > 1:
        st.header(f"📊 Batch Results — {len(completed)} videos")
        tabs = st.tabs([f"🎥 {j['filename'][:30]}" for j in completed])
        for tab, job in zip(tabs, completed):
            with tab:
                _render_job_results(job["job_id"], job.get("results"), job.get("json_data"))
        st.divider()
        _render_batch_download(completed)
    else:
        job = completed[0]
        _render_job_results(job["job_id"], job.get("results"), job.get("json_data"))


def _render_job_results(job_id: str, results, json_data):
    if not results:
        st.info("No results available.")
        return

    summary = results.get("summary", {})

    # ── Summary metrics ──
    st.header("📊 Summary")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Video Class", summary.get("video_class", "unknown"))
    c2.metric("Road Type", summary.get("road_type", "unknown"))
    c3.metric("Total Objects", summary.get("total_objects", 0))
    c4.metric("Hazard Events", summary.get("num_hazards", 0))
    st.divider()

    # ── Frame galleries ──
    frames_list = st.session_state.api_client.get_frames_list(job_id)
    if frames_list:
        tab_sel, tab_ann = st.tabs(["🖼️ Selected Frames", "🔍 Annotated Frames"])

        with tab_sel:
            cols = st.columns(min(len(frames_list), 3))
            for i, frame_info in enumerate(frames_list):
                fidx = frame_info["frame_idx"]
                ts = frame_info.get("timestamp_ms", 0)
                img_bytes = st.session_state.api_client.get_frame_image(job_id, fidx)
                if img_bytes:
                    cols[i % 3].image(img_bytes,
                                      caption=f"Frame {fidx} @ {ts:.0f}ms",
                                      use_container_width=True)

        with tab_ann:
            ann_frames = [f for f in frames_list if f.get("has_annotated")]
            if ann_frames:
                cols = st.columns(min(len(ann_frames), 3))
                for i, frame_info in enumerate(ann_frames):
                    fidx = frame_info["frame_idx"]
                    ts = frame_info.get("timestamp_ms", 0)
                    img_bytes = st.session_state.api_client.get_annotated_frame_image(job_id, fidx)
                    if img_bytes:
                        cols[i % 3].image(img_bytes,
                                          caption=f"Annotated {fidx} @ {ts:.0f}ms",
                                          use_container_width=True)
            else:
                st.info("No annotated frames available.")
        st.divider()

    # ── Classification & Hazard Events ──
    if json_data and isinstance(json_data, dict):
        client_config = json_data.get("client_config")
        if client_config:
            st.subheader("📋 Classification Result")
            cc1, cc2, cc3 = st.columns(3)
            cc1.markdown(f"**Video Class:** `{client_config.get('video_class', 'unknown')}`")
            cc2.markdown(f"**Road:** `{client_config.get('road', 'unknown')}`")
            cc3.markdown(f"**Speed:** `{client_config.get('speed', 'unknown')}`")
            prompt = client_config.get("trial_start_prompt", "")
            if prompt:
                st.markdown(f"> {prompt}")

        if json_data.get("hazard_events"):
            st.subheader("⚠️ Hazard Events")
            sev_icons = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢", "None": "⚪"}
            for i, h in enumerate(json_data["hazard_events"], 1):
                sev = h.get("hazard_severity", "None")
                icon = sev_icons.get(sev, "⚠️")
                with st.expander(f"{icon} Hazard {i}: {h.get('hazard_type', '')} ({sev})", expanded=(i <= 2)):
                    st.markdown(f"**Time:** {h.get('start_time_ms', 0):.0f}ms")
                    st.markdown(f"**Description:** {h.get('hazard_description', '')}")
                    st.markdown(f"**Road Conditions:** {h.get('road_conditions', '')}")
        st.divider()

    # ── Downloads ──
    st.subheader("💾 Downloads")
    dc1, dc2, dc3 = st.columns(3)
    if json_data and isinstance(json_data, dict):
        client_config = json_data.get("client_config")
        if client_config:
            dc1.download_button(
                "📄 config.json", data=json.dumps(client_config, indent=2),
                file_name="config.json", mime="application/json",
                use_container_width=True, key=f"dl_config_{job_id}")
        det_summary = json_data.get("detection_summary")
        if det_summary:
            dc2.download_button(
                "📊 detection_summary.json", data=json.dumps(det_summary, indent=2),
                file_name="detection_summary.json", mime="application/json",
                use_container_width=True, key=f"dl_summary_{job_id}")
        dc3.download_button(
            "📦 Full Output JSON", data=json.dumps(json_data, indent=2),
            file_name="output.json", mime="application/json",
            use_container_width=True, key=f"dl_full_{job_id}")


def _render_batch_download(completed_jobs):
    st.subheader("📦 Batch Download")
    if st.button("⬇️ Download All JSONs (ZIP)", key="dl_batch_zip"):
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for job in completed_jobs:
                jd = job.get("json_data")
                if jd:
                    fname = job.get("filename", job["job_id"])
                    stem = os.path.splitext(fname)[0]
                    zf.writestr(f"{stem}_output.json", json.dumps(jd, indent=2))
                    cc = jd.get("client_config")
                    if cc:
                        zf.writestr(f"{stem}_config.json", json.dumps(cc, indent=2))
        buf.seek(0)
        st.download_button("📥 Download ZIP", data=buf.getvalue(),
                          file_name="lightship_batch_results.zip", mime="application/zip",
                          key="dl_batch_zip_file")


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

    if st.button("🔄 Refresh", key="hist_refresh"):
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
                cc = json_data.get("client_config")
                if cc:
                    st.download_button("⬇️ config.json", data=json.dumps(cc, indent=2),
                                      file_name="config.json", mime="application/json",
                                      key=f"hist_dl_config_{job_id}")
                ds = json_data.get("detection_summary")
                if ds:
                    st.download_button("📊 detection_summary.json", data=json.dumps(ds, indent=2),
                                      file_name="detection_summary.json", mime="application/json",
                                      key=f"hist_dl_summary_{job_id}")
            else:
                st.warning("Results no longer in memory.")
                s3_uri = job.get("s3_results_uri", "")
                if s3_uri:
                    st.code(s3_uri, language="text")
        elif job.get("status") == "FAILED":
            st.error(f"Failed: {job.get('error_message', 'Unknown')}")
        else:
            st.info(f"Status: {job.get('status', 'UNKNOWN')}")


if __name__ == "__main__":
    main()
