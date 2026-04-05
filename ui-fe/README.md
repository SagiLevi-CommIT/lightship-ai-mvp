# Lightship MVP - Streamlit UI

Professional web interface for the Lightship MVP object detection and hazard labeling system.

## 🚀 Quick Start

### 1. Start the Backend API Server

From the project root:

```powershell
# Activate venv
.\.venv\Scripts\Activate.ps1

# Start API server
python src/api_server.py
```

The API will be available at `http://localhost:8000`

### 2. Start the Streamlit UI

In a new terminal:

```powershell
# Navigate to UI directory
cd ui

# Install UI dependencies (first time only)
pip install -r requirements.txt

# Start Streamlit app
streamlit run src/streamlit_app.py
```

The UI will open in your browser at `http://localhost:8501`

---

## 📖 Features

### Upload & Process Tab
- **Video Upload**: Drag & drop or browse for dashcam videos (MP4, AVI, MOV)
- **Real-time Progress**: Live progress bar with step-by-step status updates
- **Configuration**: Adjust snapshot strategy, max snapshots, and other settings

### Results Tab
- **Summary Metrics**: Total objects, snapshots analyzed, priority hazards
- **Threat Distribution**: Visual chart showing object breakdown by threat level
- **Annotated Frames**: View frames with bounding boxes and labels
- **Object Details**: Table with all detected objects per frame
- **Filter by Threat Level**: Show/hide objects based on threat levels

### Downloads
- **JSON Output**: Download structured detection results
- **Annotated Frames**: Get ZIP file with all annotated images
- **Complete Package**: Download JSON + annotated frames + original frames in one ZIP
- **Original Frames**: Access unmodified extracted frames

### Configuration Sidebar
- **Snapshot Strategy**: Choose between naive (uniform) or scene_change (CV-based)
- **Max Snapshots**: Control number of frames to extract (1-10)
- **Display Settings**: Adjust bbox thickness, font scale, and threat level filters
- **API Status**: Real-time connection status indicator

---

## 🎨 UI Structure

```
ui/
├── src/
│   ├── streamlit_app.py    # Main Streamlit application
│   ├── api_client.py        # REST API client for backend
│   └── visualization.py     # Frame annotation and drawing
├── requirements.txt         # UI dependencies
└── README.md               # This file
```

---

## 🔧 Configuration

### Backend API URL

By default, the UI connects to `http://localhost:8000`. To change:

Edit `ui/src/streamlit_app.py`:
```python
if 'api_client' not in st.session_state:
    st.session_state.api_client = APIClient(base_url="http://your-server:8000")
```

### Streamlit Settings

Create `.streamlit/config.toml` for custom settings:
```toml
[server]
port = 8501
maxUploadSize = 500

[theme]
primaryColor = "#1f77b4"
backgroundColor = "#ffffff"
secondaryBackgroundColor = "#f0f2f6"
```

---

## 🎯 How to Use

### Basic Workflow

1. **Start Backend**: Ensure API server is running
2. **Open UI**: Access Streamlit app in browser
3. **Upload Video**: Select a dashcam video file
4. **Configure** (optional): Adjust settings in sidebar
5. **Process**: Click "Start Processing" button
6. **Monitor**: Watch real-time progress
7. **View Results**: Switch to Results tab when complete
8. **Download**: Get JSON and/or annotated images

### Processing Status Messages

- ⏳ **Queued**: Video uploaded, waiting to start
- 🔄 **Initializing**: Setting up pipeline
- 📹 **Loading video**: Extracting metadata
- 🎬 **Selecting snapshots**: Choosing key frames
- 🖼️ **Extracting frames**: Saving frame images
- 🤖 **Labeling frame X/Y**: LLM processing (15-20s per frame)
- 💾 **Merging results**: Creating final output
- ✅ **Completed**: Ready to view results

### Annotated Frame Display

Similar to ground truth annotations, frames show:
- **Bounding boxes**: Color-coded by threat level
  - 🔴 Red: Critical
  - 🟠 Orange: High
  - 🟡 Yellow: Medium
  - 🟢 Green: Low
  - ⚪ Gray: None
- **Labels**: Object description + distance
- **Timestamp**: Frame timestamp overlay
- **Object count**: Total objects in frame

---

## 🐛 Troubleshooting

### "❌ Disconnected" API Status

**Problem**: UI cannot reach backend API

**Solutions**:
1. Check API server is running: `python src/api_server.py`
2. Verify API is on port 8000
3. Check firewall settings
4. Try accessing `http://localhost:8000/health` in browser

### Upload Fails

**Problem**: Video upload returns error

**Solutions**:
1. Check video format (must be MP4, AVI, or MOV)
2. Verify video file is not corrupted
3. Check file size (default limit: 500MB)
4. Ensure sufficient disk space in temp directory

### Processing Stuck

**Problem**: Progress bar doesn't move

**Solutions**:
1. Check API server logs for errors
2. Verify AWS credentials in `.env`
3. Check Bedrock API limits/quotas
4. Restart API server if needed

### No Annotated Frames Displayed

**Problem**: Results tab shows no images

**Solutions**:
1. Verify processing completed successfully
2. Check temp directory has write permissions
3. Ensure OpenCV is installed correctly
4. Try re-processing the video

---

## 📊 Performance

### Expected Processing Times

- **Video Upload**: 1-5 seconds (depends on size)
- **Frame Extraction**: 1-2 seconds for 3 frames
- **LLM Labeling**: 15-20 seconds per frame
- **Total for 3 frames**: ~45-60 seconds

### Resource Usage

- **CPU**: Moderate (frame extraction, annotation)
- **Memory**: 500MB-1GB per concurrent job
- **Network**: ~10MB upload per minute of video
- **Disk**: ~50MB temp space per video

---

## 🎨 Customization

### Change Color Scheme

Edit `visualization.py`:
```python
THREAT_COLORS = {
    'critical': (0, 0, 255),      # Your color (BGR)
    'high': (0, 165, 255),
    # ...
}
```

### Add Custom UI Elements

Streamlit makes it easy to extend. Example:
```python
# In streamlit_app.py, add to sidebar:
with st.sidebar:
    st.subheader("Custom Settings")
    my_setting = st.checkbox("Enable feature X")
```

### Modify Annotation Style

Edit `visualization.py` methods:
- `draw_bbox()`: Change box style
- `draw_label()`: Modify label format
- `get_color_for_object()`: Custom coloring logic

---

## 🔐 Security Notes

- **API Server**: Currently no authentication (add JWT/API keys for production)
- **File Upload**: Validate file types and sizes
- **Temp Files**: Automatically cleaned up after use
- **CORS**: Currently allows all origins (restrict in production)

---

## 📝 API Endpoints Reference

### Available Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | API info |
| `/health` | GET | Health check |
| `/process-video` | POST | Upload & process video |
| `/status/{job_id}` | GET | Get processing status |
| `/results/{job_id}` | GET | Get results |
| `/download/json/{job_id}` | GET | Download JSON |
| `/download/frame/{job_id}/{frame_idx}` | GET | Download frame |
| `/cleanup/{job_id}` | DELETE | Cleanup temp files |

See `api_server.py` for full API documentation.

---

## 🎓 Tips & Best Practices

1. **Start with small videos** (<1 min) for testing
2. **Use naive strategy first** - faster, good for uniform content
3. **Try scene_change for varied content** - better coverage but slower
4. **Adjust max_snapshots based on video length**
5. **Download results before closing UI** - temp files may be cleaned up
6. **Monitor API logs** for debugging: `python src/api_server.py`

---

**Version:** 1.0.0
**Status:** Production-Ready
**Framework:** Streamlit 1.28+
**Backend:** FastAPI

