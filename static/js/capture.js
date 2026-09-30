let stream = null;
let capturedCount = 0;
const TARGET = 40;

const video = document.getElementById('video');
const canvas = document.getElementById('canvas');
const progressBar = document.getElementById('progress-bar');
const progressText = document.getElementById('progress-text');
const statusText = document.getElementById('status-text');
const startBtn = document.getElementById('start-btn');
const doneBtn = document.getElementById('done-btn');

async function initCamera() {
    try {
        stream = await navigator.mediaDevices.getUserMedia({
            video: { width: 640, height: 480 }
        });
        video.srcObject = stream;
        await video.play();
        statusText.textContent = '✅ Camera ready. Click "Start Capture" to begin.';
        statusText.style.color = '#10b981';
        startBtn.disabled = false;
    } catch (err) {
        statusText.textContent = '❌ Camera error: ' + err.message + ' — Please allow camera access.';
        statusText.style.color = '#ef4444';
    }
}

function updateProgress() {
    const percent = (capturedCount / TARGET) * 100;
    progressBar.style.width = percent + '%';
    progressText.textContent = capturedCount + ' / ' + TARGET + ' images';
}

async function captureFrame() {
    if (capturedCount >= TARGET) {
        finishCapture();
        return;
    }
    
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    
    const imageData = canvas.toDataURL('image/jpeg', 0.8);
    
    try {
        const response = await fetch('/admin/capture_face', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                image: imageData,
                student_id: STUDENT_ID
            })
        });
        
        const result = await response.json();
        if (result.success) {
            capturedCount = result.count;
            updateProgress();
            statusText.textContent = '📸 Capturing... Move your head slowly left/right/up/down.';
            statusText.style.color = '#3498db';
        }
    } catch (err) {
        console.error('Error:', err);
    }
    
    setTimeout(captureFrame, 150);
}

function startCapture() {
    startBtn.disabled = true;
    startBtn.textContent = 'Capturing... Please wait';
    statusText.textContent = '🎥 Look at the camera. Move your head slowly.';
    statusText.style.color = '#3498db';
    captureFrame();
}

function finishCapture() {
    statusText.textContent = '✅ Capture complete! All 40 images saved. Now enroll voice.';
    statusText.style.color = '#10b981';
    doneBtn.style.display = 'inline-block';
    doneBtn.textContent = 'Next: Enroll Voice →';
    
    if (stream) {
        stream.getTracks().forEach(track => track.stop());
    }
}

initCamera();