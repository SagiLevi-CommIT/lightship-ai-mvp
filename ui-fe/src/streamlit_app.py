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

# Page config
st.set_page_config(
    page_title="Lightship MVP - Object & Hazard Detection",
    page_icon="🚗",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for professional look
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #1f77b4;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        font-size: 1.2rem;
        color: #666;
        margin-bottom: 2rem;
    }
    .metric-card {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 0.5rem;
        margin: 0.5rem 0;
    }
    .stProgress > div > div > div > div {
        background-color: #1f77b4;
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
</style>
""", unsafe_allow_html=True)

# Initialize session state
if 'api_client' not in st.session_state:
    st.session_state.api_client = APIClient()
if 'job_id' not in st.session_state:
    st.session_state.job_id = None
if 'results' not in st.session_state:
    st.session_state.results = None
if 'annotated_frames' not in st.session_state:
    st.session_state.annotated_frames = []
if 'json_data' not in st.session_state:
    st.session_state.json_data = None


def main():
    """Main Streamlit app."""

    # Header
    st.markdown('<div class="main-header">🚗 Lightship MVP</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub-header">Snapshot-Based Object Detection & Priority Hazard Identification</div>',
        unsafe_allow_html=True
    )

    # Sidebar - Configuration
    with st.sidebar:
        st.header("⚙️ Configuration")

        st.subheader("Pipeline Settings")

        use_cv_labeler = st.checkbox(
            "Use V2 Pipeline (CV + Temporal LLM)",
            value=True,
            help="V2: CV Models + Hazard LLM | V1: LLM Image Analysis"
        )

        st.subheader("Processing Settings")

        snapshot_strategy = st.selectbox(
            "Snapshot Strategy",
            options=["naive", "scene_change"],
            help="Naive: Uniform sampling | Scene Change: CV-based detection"
        )

        max_snapshots = st.slider(
            "Max Snapshots",
            min_value=1,
            max_value=10,
            value=3,
            help="Number of frames to extract and analyze"
        )

        if use_cv_labeler:
            st.subheader("V2 Hazard Assessment")

            hazard_mode = st.selectbox(
                "Hazard LLM Mode",
                options=["sliding_window", "full_video"],
                help="Sliding window: Process in 3-frame windows | Full video: Analyze entire sequence"
            )

            if hazard_mode == "sliding_window":
                window_size = st.slider(
                    "Window Size (frames)",
                    min_value=2,
                    max_value=5,
                    value=3,
                    help="Number of frames per window"
                )

                window_overlap = st.slider(
                    "Window Overlap (frames)",
                    min_value=0,
                    max_value=window_size-1,
                    value=1,
                    help="Overlap between consecutive windows"
                )
            else:
                window_size = 3
                window_overlap = 1

        st.divider()

        st.subheader("Evaluation Settings")

        iou_threshold = st.slider(
            "IoU Threshold",
            min_value=0.1,
            max_value=0.7,
            value=0.3,
            step=0.05,
            help="Intersection over Union threshold for object matching (lower = more lenient)"
        )

        use_center_distance = st.checkbox(
            "Use Center Distance Matching",
            value=False,
            help="Also match objects based on center point distance (good for small objects)"
        )

        if use_center_distance:
            center_distance_threshold = st.slider(
                "Center Distance Threshold (px)",
                min_value=10,
                max_value=100,
                value=50,
                step=10,
                help="Maximum center point distance in pixels"
            )
        else:
            center_distance_threshold = 50

        st.divider()

        st.subheader("Display Settings")

        show_priorities = st.multiselect(
            "Filter by Priority",
            options=["critical", "high", "medium", "low", "none"],
            default=["critical", "high", "medium", "low", "none"],
            help="Select which threat levels to display"
        )

        bbox_thickness = st.slider(
            "Bounding Box Thickness",
            min_value=1,
            max_value=5,
            value=2
        )

        font_scale = st.slider(
            "Label Font Scale",
            min_value=0.3,
            max_value=1.5,
            value=0.6,
            step=0.1
        )

        st.divider()

        # API Status
        st.subheader("🔌 API Status")
        try:
            if st.session_state.api_client.check_health():
                st.success("✅ Connected")
            else:
                st.error("❌ Disconnected")
                st.info("Make sure the API server is running:\n```python src/api_server.py```")
        except:
            st.error("❌ Disconnected")
            st.info("Make sure the API server is running:\n```python src/api_server.py```")

    # Collect V2 config if enabled
    v2_config = None
    if use_cv_labeler:
        v2_config = {
            'hazard_mode': hazard_mode,
            'window_size': window_size,
            'window_overlap': window_overlap
        }

    # Main content
    tab1, tab2, tab3, tab4 = st.tabs(["📤 Upload & Process", "📊 Results", "📈 Evaluation", "ℹ️ About"])

    with tab1:
        upload_and_process_tab(snapshot_strategy, max_snapshots, use_cv_labeler, v2_config)

    with tab2:
        results_tab(show_priorities, bbox_thickness, font_scale)

    with tab3:
        evaluation_tab(iou_threshold, use_center_distance, center_distance_threshold)

    with tab4:
        about_tab()


def upload_and_process_tab(snapshot_strategy, max_snapshots, use_cv_labeler, v2_config):
    """Upload and process video tab."""

    st.header("Upload Video")

    uploaded_file = st.file_uploader(
        "Choose a dashcam video",
        type=['mp4', 'avi', 'mov'],
        help="Supported formats: MP4, AVI, MOV"
    )

    if uploaded_file is not None:
        col1, col2 = st.columns([2, 1])

        with col1:
            st.video(uploaded_file)

        with col2:
            st.markdown('<div class="info-box">', unsafe_allow_html=True)
            st.write("**Video Information**")
            st.write(f"📁 Filename: `{uploaded_file.name}`")
            st.write(f"📏 Size: {uploaded_file.size / 1024 / 1024:.2f} MB")
            st.write(f"🔧 Pipeline: {'V2 (CV)' if use_cv_labeler else 'V1 (LLM)'}")
            st.markdown('</div>', unsafe_allow_html=True)

            st.divider()

            if st.button("🚀 Start Processing", type="primary", width="stretch"):
                process_video(uploaded_file, snapshot_strategy, max_snapshots, use_cv_labeler, v2_config)

    # Show processing status
    if st.session_state.job_id:
        show_processing_status()


def process_video(uploaded_file, snapshot_strategy, max_snapshots, use_cv_labeler, v2_config):
    """Process uploaded video."""

    config = {
        "snapshot_strategy": snapshot_strategy,
        "max_snapshots": max_snapshots,
        "cleanup_frames": False,
        "use_cv_labeler": use_cv_labeler
    }

    # Add V2 config if enabled
    if v2_config:
        config.update(v2_config)

    with st.spinner("Uploading video..."):
        job_id = st.session_state.api_client.upload_video(
            uploaded_file,
            config
        )

    if job_id:
        st.session_state.job_id = job_id
        st.success(f"✅ Video uploaded! Job ID: {job_id}")
        st.rerun()
    else:
        st.error("❌ Failed to upload video. Check API connection.")


def show_processing_status():
    """Display processing status with progress bar."""

    st.divider()
    st.subheader("Processing Status")

    status_placeholder = st.empty()
    progress_bar = st.progress(0)
    message_placeholder = st.empty()

    # Poll for status updates
    max_polls = 300  # 5 minutes max
    poll_count = 0

    while poll_count < max_polls:
        status = st.session_state.api_client.get_status(st.session_state.job_id)

        if not status:
            st.error("Failed to get status")
            break

        # Update progress
        progress = status.get('progress', 0.0)
        progress_bar.progress(progress)

        # Update message
        current_status = status.get('status', 'unknown')
        message = status.get('message', 'Processing...')
        current_step = status.get('current_step', '')

        if current_status == "processing":
            message_placeholder.info(f"⏳ {message}")
        elif current_status == "completed":
            message_placeholder.success(f"✅ {message}")

            # Fetch results
            results = st.session_state.api_client.get_results(st.session_state.job_id)
            if results:
                st.session_state.results = results

                # Generate annotated frames
                generate_annotated_frames(results)

                st.success("🎉 Processing completed! Go to the Results tab to view outputs.")

                # Clear job_id to stop polling
                st.session_state.job_id = None

                # Force one final rerun to update UI state
                time.sleep(0.5)
                st.rerun()
            break

        elif current_status == "failed":
            message_placeholder.error(f"❌ {message}")
            st.session_state.job_id = None
            break

        time.sleep(1)
        poll_count += 1

    if poll_count >= max_polls:
        st.error("⏱️ Processing timeout - please check API logs")
        st.session_state.job_id = None


def generate_annotated_frames(results):
    """Generate annotated frames with bounding boxes and labels."""

    visualizer = FrameVisualizer()
    annotated_frames = []

    # Load output JSON
    with st.spinner("Generating annotated frames..."):
        json_data = st.session_state.api_client.get_json_content(st.session_state.job_id)

        if json_data:
            # Store JSON data in session state for later use
            st.session_state.json_data = json_data

            for snapshot in results['snapshots']:
                frame_path = snapshot['frame_path']
                timestamp = snapshot['timestamp_ms']

                # Filter objects for this timestamp
                objects_at_timestamp = [
                    obj for obj in json_data['objects']
                    if abs(obj['start_time_ms'] - timestamp) < 1.0
                ]

                # Annotate frame
                annotated_img, annotated_path = visualizer.annotate_frame(
                    frame_path,
                    objects_at_timestamp,
                    timestamp
                )

                annotated_frames.append({
                    'timestamp': timestamp,
                    'frame_idx': snapshot['frame_idx'],
                    'original_path': frame_path,
                    'annotated_path': annotated_path,
                    'annotated_image': annotated_img,
                    'objects': objects_at_timestamp
                })

    st.session_state.annotated_frames = annotated_frames


def results_tab(show_priorities, bbox_thickness, font_scale):
    """Display processing results."""

    if not st.session_state.results:
        st.info("👆 Upload and process a video to see results here.")
        return

    results = st.session_state.results

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
        num_hazards = summary.get('num_hazards', 0)
        st.metric("Hazard Events", num_hazards)

    st.divider()

    # Hazard Events Section (V2 only)
    # Use cached JSON data from session state instead of fetching with potentially null job_id
    json_data = st.session_state.get('json_data')
    if json_data and 'hazard_events' in json_data and len(json_data['hazard_events']) > 0:
        st.header("⚠️ Hazard Events")

        for i, hazard in enumerate(json_data['hazard_events'], 1):
            severity = hazard['hazard_severity']
            severity_colors = {
                'Critical': '🔴',
                'High': '🟠',
                'Medium': '🟡',
                'Low': '🟢',
                'None': '⚪'
            }
            icon = severity_colors.get(severity, '⚠️')

            with st.expander(f"{icon} Hazard {i}: {hazard['hazard_type']} (Severity: {severity})", expanded=(i<=2)):
                st.markdown(f"**Time:** {hazard['start_time_ms']:.0f}ms")
                st.markdown(f"**Severity:** {severity}")
                st.markdown(f"**Type:** {hazard['hazard_type']}")
                st.markdown(f"**Description:** {hazard['hazard_description']}")
                st.markdown(f"**Road Conditions:** {hazard['road_conditions']}")

        st.divider()

    # Priority distribution
    st.subheader("Priority Distribution")
    priority_dist = summary.get('priority_distribution', {})

    if priority_dist:
        col1, col2 = st.columns([2, 1])

        with col1:
            import plotly.graph_objects as go

            colors = {
                'critical': '#d62728',
                'high': '#ff7f0e',
                'medium': '#ffbb78',
                'low': '#98df8a',
                'none': '#c7c7c7'
            }

            fig = go.Figure(data=[go.Bar(
                x=list(priority_dist.keys()),
                y=list(priority_dist.values()),
                marker_color=[colors.get(k, '#1f77b4') for k in priority_dist.keys()]
            )])

            fig.update_layout(
                title="Objects by Priority",
                xaxis_title="Priority",
                yaxis_title="Count",
                height=300
            )

            st.plotly_chart(fig, width="stretch", key="priority_chart")

        with col2:
            st.markdown("**Priority Levels:**")
            for level, count in sorted(priority_dist.items(), key=lambda x: x[1], reverse=True):
                st.write(f"- **{level.upper()}**: {count}")

    st.divider()

    # Annotated frames
    st.header("🖼️ Annotated Frames")

    if st.session_state.annotated_frames:
        for i, frame_data in enumerate(st.session_state.annotated_frames):
            with st.expander(
                f"Frame {i+1} @ {frame_data['timestamp']:.2f}ms - {len(frame_data['objects'])} objects",
                expanded=(i==0)
            ):
                # Display annotated image
                st.image(
                    frame_data['annotated_image'],
                    caption=f"Frame {frame_data['frame_idx']} at {frame_data['timestamp']:.2f}ms",
                    width="stretch"
                )

                # Object details table
                st.subheader("Detected Objects")

                # Filter by priority
                filtered_objects = [
                    obj for obj in frame_data['objects']
                    if obj['priority'] in show_priorities
                ]

                if filtered_objects:
                    import pandas as pd

                    df = pd.DataFrame([{
                        'Description': obj['description'],
                        'Priority': obj['priority'].upper(),
                        'Distance': obj['distance'],
                        'Center': f"({obj['center']['x']}, {obj['center']['y']})"
                    } for obj in filtered_objects])

                    st.dataframe(df, width="stretch", hide_index=True)
                else:
                    st.info("No objects match the selected priority filters.")

    st.divider()

    # Download section
    st.header("💾 Downloads")

    col1, col2, col3 = st.columns(3)

    with col1:
        # Download JSON (use cached data if available)
        json_content = st.session_state.get('json_data')
        if json_content:
            st.download_button(
                label="📄 Download JSON",
                data=json.dumps(json_content, indent=2),
                file_name="lightship_output.json",
                mime="application/json",
                width="stretch"
            )

    with col2:
        # Download annotated frames as zip
        zip_data = create_annotated_frames_zip()
        if zip_data:
            st.download_button(
                label="🖼️ Download Annotated Frames (ZIP)",
                data=zip_data,
                file_name="annotated_frames.zip",
                mime="application/zip",
                width="stretch"
            )

    with col3:
        # Download all (JSON + frames + originals)
        zip_data = create_complete_zip()
        if zip_data:
            st.download_button(
                label="📦 Download All Files (ZIP)",
                data=zip_data,
                file_name="lightship_complete_output.zip",
                mime="application/zip",
                width="stretch"
            )


def create_annotated_frames_zip():
    """Create ZIP file with annotated frames."""
    zip_buffer = BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for frame_data in st.session_state.annotated_frames:
            if os.path.exists(frame_data['annotated_path']):
                zip_file.write(
                    frame_data['annotated_path'],
                    arcname=f"frame_{frame_data['frame_idx']}_annotated.png"
                )

    zip_buffer.seek(0)
    return zip_buffer.getvalue()


def create_complete_zip():
    """Create ZIP file with all outputs."""
    zip_buffer = BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        # Add JSON (use cached data if available)
        json_content = st.session_state.get('json_data')
        if json_content:
            zip_file.writestr(
                "output.json",
                json.dumps(json_content, indent=2)
            )

        # Add annotated frames
        for frame_data in st.session_state.annotated_frames:
            if os.path.exists(frame_data['annotated_path']):
                zip_file.write(
                    frame_data['annotated_path'],
                    arcname=f"annotated/frame_{frame_data['frame_idx']}.png"
                )

            # Add original frames
            if os.path.exists(frame_data['original_path']):
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

