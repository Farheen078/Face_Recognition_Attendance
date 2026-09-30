let stream = null;
let sessionActive = false;
let captureInterval = null;
const CAPTURE_INTERVAL_MS = 700;
const LAST_MARKED = {};

// Voice recording
let voiceMediaRecorder = null;
let voiceChunks = [];
let voiceStream = null;
let voiceTimerInterval = null;
let currentSubjectId = null;

const video = document.getElementById('video');
const canvas = document.getElementById('canvas');
const statusText = document.getElementById('status-text');
const subjectSelect = document.getElementById('subject-select');
const startBtn = document.getElementById('start-btn');
const stopBtn = document.getElementById('stop-btn');
const countEl = document.getElementById('count');
const listContainer = document.getElementById('attendance-list-items');
const voiceFallback = document.getElementById('voice-fallback');
const voiceRecordBtn = document.getElementById('voice-record-btn');
const voiceStopBtn = document.getElementById('voice-stop-btn');
const voiceStatus = document.getElementById('voice-status');


async function initCamera() {
    try {
        stream = await navigator.mediaDevices.getUserMedia({
            video: { width: 640, height: 480 }
        });
        video.srcObject = stream;
        await video.play();
    } catch (err) {
        statusText.textContent = '❌ Camera error: ' + err.message;
        statusText.style.color = '#ef4444';
    }
}


async function startSession() {
    const subjectId = subjectSelect.value;
    if (!subjectId) {
        alert('Please select a subject first!');
        return;
    }
    
    sessionActive = true;
    currentSubjectId = subjectId;
    startBtn.style.display = 'none';
    stopBtn.style.display = 'inline-block';
    subjectSelect.disabled = true;
    voiceFallback.style.display = 'block';
    
    statusText.textContent = '🎥 Session started. Face the camera and blink naturally.';
    statusText.style.color = '#10b981';
    
    await loadTodayAttendance(subjectId);
    
    captureInterval = setInterval(() => {
        captureAndSend(subjectId);
    }, CAPTURE_INTERVAL_MS);
}


function stopSession() {
    sessionActive = false;
    startBtn.style.display = 'inline-block';
    stopBtn.style.display = 'none';
    subjectSelect.disabled = false;
    voiceFallback.style.display = 'none';
    
    if (captureInterval) {
        clearInterval(captureInterval);
        captureInterval = null;
    }
    
    statusText.textContent = '⏹️ Session stopped.';
    statusText.style.color = '#64748b';
}


async function captureAndSend(subjectId) {
    if (!sessionActive) return;
    if (video.readyState !== 4) return;
    
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    
    const imageData = canvas.toDataURL('image/jpeg', 0.7);
    
    try {
        const response = await fetch('/admin/attendance/mark', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                image: imageData,
                subject_id: subjectId
            })
        });
        
        const result = await response.json();
        
        updateStatusFromResult(result);
        
        if (result.success && result.student_name) {
            const key = result.student_name;
            const now = Date.now();
            if (!LAST_MARKED[key] || now - LAST_MARKED[key] > 30000) {
                LAST_MARKED[key] = now;
                await loadTodayAttendance(subjectId);
            }
        }
    } catch (err) {
        console.error('Error:', err);
    }
}


function updateStatusFromResult(result) {
    if (result.success) {
        statusText.textContent = result.message + ' (Liveness ✓)';
        statusText.style.color = '#10b981';
        return;
    }
    
    if (result.status === 'no_face') {
        statusText.textContent = '👀 Waiting for face...';
        statusText.style.color = '#64748b';
    } else if (result.status === 'unknown') {
        statusText.textContent = '❓ Face not recognized — Use Voice Fallback below';
        statusText.style.color = '#f59e0b';
    } else if (result.status === 'collecting') {
        statusText.textContent = `🔍 Detecting liveness... (${result.frames}/5 frames)`;
        statusText.style.color = '#6366f1';
    } else if (result.status === 'blink_required') {
        statusText.textContent = '👁️ Please blink your eyes naturally';
        statusText.style.color = '#f59e0b';
    } else if (result.status === 'already_marked') {
        statusText.textContent = 'ℹ️ ' + result.message;
        statusText.style.color = '#f59e0b';
    }
}


async function loadTodayAttendance(subjectId) {
    try {
        const response = await fetch(`/admin/attendance/today/${subjectId}`);
        const data = await response.json();
        
        if (!data.success) return;
        
        countEl.textContent = data.attendance.length;
        
        if (data.attendance.length === 0) {
            listContainer.innerHTML = '<p style="color: #64748b;">No attendance marked yet.</p>';
            return;
        }
        
        let html = '';
        for (const a of data.attendance) {
            const icon = a.method === 'voice' ? '🎤' : '🟢';
            html += `
                <div class="attendance-item">
                    <div>
                        <strong>${a.name} ${icon}</strong>
                        <small>Roll: ${a.roll_no}</small>
                    </div>
                    <div class="att-time">${a.time}</div>
                </div>
            `;
        }
        listContainer.innerHTML = html;
    } catch (err) {
        console.error('Error loading attendance:', err);
    }
}


// ==================== VOICE FALLBACK ====================

async function startVoiceRecording() {
    try {
        voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        
        voiceMediaRecorder = new MediaRecorder(voiceStream);
        voiceChunks = [];
        
        voiceMediaRecorder.ondataavailable = (event) => {
            voiceChunks.push(event.data);
        };
        
        voiceMediaRecorder.onstop = () => {
            const audioBlob = new Blob(voiceChunks, { type: 'audio/webm' });
            sendVoiceToServer(audioBlob);
        };
        
        voiceMediaRecorder.start();
        
        voiceRecordBtn.style.display = 'none';
        voiceStopBtn.style.display = 'inline-block';
        voiceStatus.textContent = '🎙️ Recording... Speak clearly: "My name is..."';
        voiceStatus.style.color = '#dc2626';
        
        let remaining = 3;
        voiceTimerInterval = setInterval(() => {
            remaining--;
            voiceStatus.textContent = `🎙️ Recording... ${remaining}s remaining`;
            
            if (remaining <= 0) {
                clearInterval(voiceTimerInterval);
                if (voiceMediaRecorder && voiceMediaRecorder.state === 'recording') {
                    voiceMediaRecorder.stop();
                }
            }
        }, 1000);
        
    } catch (err) {
        voiceStatus.textContent = '❌ Mic error: ' + err.message;
        voiceStatus.style.color = '#dc2626';
    }
}


function stopVoiceRecording() {
    if (voiceMediaRecorder && voiceMediaRecorder.state === 'recording') {
        voiceMediaRecorder.stop();
        clearInterval(voiceTimerInterval);
    }
    voiceStopBtn.style.display = 'none';
}


async function sendVoiceToServer(audioBlob) {
    if (!currentSubjectId) {
        voiceStatus.textContent = '❌ No subject selected';
        voiceStatus.style.color = '#dc2626';
        return;
    }
    
    voiceStatus.textContent = '⏳ Verifying voice...';
    voiceStatus.style.color = '#1e40af';
    
    const formData = new FormData();
    formData.append('audio', audioBlob, 'voice.webm');
    formData.append('subject_id', currentSubjectId);
    
    try {
        const response = await fetch('/admin/attendance/voice_mark', {
            method: 'POST',
            body: formData
        });
        
        const result = await response.json();
        
        if (result.success) {
            voiceStatus.textContent = '✅ ' + result.message + ' (Similarity: ' + result.similarity + '%)';
            voiceStatus.style.color = '#059669';
            await loadTodayAttendance(currentSubjectId);
        } else {
            voiceStatus.textContent = '❌ ' + result.message;
            voiceStatus.style.color = '#dc2626';
        }
    } catch (err) {
        voiceStatus.textContent = '❌ Error: ' + err.message;
        voiceStatus.style.color = '#dc2626';
    }
    
    // Reset buttons
    voiceRecordBtn.style.display = 'inline-block';
    voiceRecordBtn.textContent = '🎙️ Record Voice Again';
    voiceStopBtn.style.display = 'none';
    
    if (voiceStream) {
        voiceStream.getTracks().forEach(track => track.stop());
    }
}


initCamera();