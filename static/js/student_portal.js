let stream = null;
let captureInterval = null;
let marked = false;
let voiceRecorder = null;
let voiceChunks = [];
let voiceStream = null;

const video = document.getElementById('video');
const canvas = document.getElementById('canvas');
const statusEl = document.getElementById('status');
const voiceBtn = document.getElementById('voice-btn');

const CAPTURE_INTERVAL = 900;

async function initCamera() {
    try {
        stream = await navigator.mediaDevices.getUserMedia({
            video: { width: 640, height: 480, facingMode: 'user' }
        });
        video.srcObject = stream;
        await video.play();
        statusEl.textContent = '👀 Look at the camera';
        startCapture();
    } catch (err) {
        statusEl.textContent = '❌ Camera error: ' + err.message;
        statusEl.className = 'kiosk-status error';
    }
}

function startCapture() {
    captureInterval = setInterval(captureAndSend, CAPTURE_INTERVAL);
}

async function captureAndSend() {
    if (marked) return;
    if (video.readyState !== 4) return;

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);

    const imageData = canvas.toDataURL('image/jpeg', 0.75);

    try {
        const response = await fetch('/student/mark', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: imageData })
        });

        const result = await response.json();
        updateStatus(result);
    } catch (err) {
        console.error('Error:', err);
    }
}

function updateStatus(result) {
    if (marked) return;

    if (result.success) {
        marked = true;

        if (result.attendance_status === 'late') {
            statusEl.textContent = `⏰ ${result.student_name} — Late Marked (${result.confidence}%)`;
            statusEl.className = 'kiosk-status late';
        } else {
            statusEl.textContent = `✅ ${result.student_name} — Present (${result.confidence}%)`;
            statusEl.className = 'kiosk-status success';
        }

        voiceBtn.classList.remove('show');

        if (captureInterval) {
            clearInterval(captureInterval);
            captureInterval = null;
        }

        setTimeout(() => resetKiosk(), 3000);
        return;
    }

    if (result.status === 'no_face') {
        statusEl.textContent = '👀 Look at the camera';
        statusEl.className = 'kiosk-status';
        voiceBtn.classList.remove('show');
    } else if (result.status === 'no_class') {
        statusEl.textContent = '🕐 Class ended';
        statusEl.className = 'kiosk-status warning';
        setTimeout(() => window.location.reload(), 2000);
    } else if (result.status === 'unknown') {
        statusEl.textContent = '❓ Face not recognized — Try Voice';
        statusEl.className = 'kiosk-status error';
        voiceBtn.classList.add('show');
    } else if (result.status === 'collecting') {
        statusEl.textContent = '🔍 Verifying liveness...';
        statusEl.className = 'kiosk-status';
        voiceBtn.classList.remove('show');
    } else if (result.status === 'blink_required') {
        statusEl.textContent = '👁️ Please blink your eyes naturally';
        statusEl.className = 'kiosk-status warning';
        voiceBtn.classList.remove('show');
    } else if (result.status === 'already_marked') {
        const st = result.attendance_status === 'late' ? 'Late' : 'Present';
        statusEl.textContent = `ℹ️ ${result.student_name} — Already Marked (${st})`;
        statusEl.className = 'kiosk-status warning';
        voiceBtn.classList.remove('show');
        marked = true;

        if (captureInterval) {
            clearInterval(captureInterval);
            captureInterval = null;
        }
        setTimeout(() => resetKiosk(), 3000);
    } else {
        statusEl.textContent = result.message || 'Please try again';
        statusEl.className = 'kiosk-status';
    }
}

function resetKiosk() {
    marked = false;
    statusEl.textContent = '👀 Look at the camera';
    statusEl.className = 'kiosk-status';
    voiceBtn.classList.remove('show');

    if (!captureInterval) startCapture();
}

async function startVoiceRecording() {
    if (marked) return;

    try {
        voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        voiceRecorder = new MediaRecorder(voiceStream);
        voiceChunks = [];

        voiceRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) voiceChunks.push(e.data);
        };

        voiceRecorder.onstop = () => {
            const blob = new Blob(voiceChunks, { type: 'audio/webm' });
            sendVoice(blob);
            if (voiceStream) {
                voiceStream.getTracks().forEach(t => t.stop());
                voiceStream = null;
            }
        };

        voiceRecorder.start();
        statusEl.textContent = '🎙️ Recording... Say: "My name is..."';
        statusEl.className = 'kiosk-status warning';

        setTimeout(() => {
            if (voiceRecorder && voiceRecorder.state === 'recording') {
                voiceRecorder.stop();
            }
        }, 3000);
    } catch (err) {
        statusEl.textContent = '❌ Mic error: ' + err.message;
        statusEl.className = 'kiosk-status error';
    }
}

async function sendVoice(blob) {
    statusEl.textContent = '⏳ Verifying voice...';
    statusEl.className = 'kiosk-status';

    const formData = new FormData();
    formData.append('audio', blob, 'voice.webm');

    try {
        const response = await fetch('/student/voice_mark', {
            method: 'POST',
            body: formData
        });

        const result = await response.json();

        if (result.success) {
            marked = true;

            if (result.attendance_status === 'late') {
                statusEl.textContent = `⏰ ${result.student_name} — Late Marked (Voice ${result.similarity}%)`;
                statusEl.className = 'kiosk-status late';
            } else {
                statusEl.textContent = `✅ ${result.student_name} — Present (Voice ${result.similarity}%)`;
                statusEl.className = 'kiosk-status success';
            }

            voiceBtn.classList.remove('show');

            if (captureInterval) {
                clearInterval(captureInterval);
                captureInterval = null;
            }

            setTimeout(() => resetKiosk(), 3000);
        } else if (result.status === 'already_marked') {
            marked = true;
            statusEl.textContent = `ℹ️ ${result.student_name} — Already Marked`;
            statusEl.className = 'kiosk-status warning';
            voiceBtn.classList.remove('show');
            setTimeout(() => resetKiosk(), 3000);
        } else {
            statusEl.textContent = '❌ ' + (result.message || 'Voice not recognized');
            statusEl.className = 'kiosk-status error';
            voiceBtn.classList.add('show');
        }
    } catch (err) {
        statusEl.textContent = '❌ Error: ' + err.message;
        statusEl.className = 'kiosk-status error';
    }
}

initCamera();