let mediaRecorder = null;
let audioChunks = [];
let stream = null;
let timerInterval = null;
let audioBlob = null;

const RECORD_DURATION = 3;

async function startRecording() {
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        
        mediaRecorder = new MediaRecorder(stream);
        audioChunks = [];
        
        mediaRecorder.ondataavailable = (event) => {
            audioChunks.push(event.data);
        };
        
        mediaRecorder.onstop = () => {
            audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
            showPreview();
        };
        
        mediaRecorder.start();
        
        document.getElementById('record-btn').style.display = 'none';
        document.getElementById('stop-btn').style.display = 'inline-block';
        document.getElementById('status').textContent = '🎙️ Recording... Please speak clearly.';
        document.getElementById('status').style.color = '#ef4444';
        
        let remaining = RECORD_DURATION;
        document.getElementById('timer-value').textContent = remaining;
        
        timerInterval = setInterval(() => {
            remaining--;
            document.getElementById('timer-value').textContent = remaining;
            
            if (remaining <= 0) {
                clearInterval(timerInterval);
                if (mediaRecorder && mediaRecorder.state === 'recording') {
                    mediaRecorder.stop();
                }
            }
        }, 1000);
        
    } catch (err) {
        document.getElementById('status').textContent = '❌ Mic error: ' + err.message;
        document.getElementById('status').style.color = '#ef4444';
    }
}

function stopRecording() {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
        clearInterval(timerInterval);
    }
    
    document.getElementById('stop-btn').style.display = 'none';
}

function showPreview() {
    if (stream) {
        stream.getTracks().forEach(track => track.stop());
    }
    
    const audioUrl = URL.createObjectURL(audioBlob);
    document.getElementById('audio-preview').src = audioUrl;
    document.getElementById('preview-section').style.display = 'block';
    document.getElementById('status').textContent = '✅ Recording complete. Listen and save.';
    document.getElementById('status').style.color = '#10b981';
}

async function saveVoice() {
    if (!audioBlob) {
        alert('Please record first.');
        return;
    }
    
    const formData = new FormData();
    formData.append('audio', audioBlob, 'voice.webm');
    formData.append('student_id', STUDENT_ID);
    
    document.getElementById('save-btn').disabled = true;
    document.getElementById('save-btn').textContent = 'Saving...';
    
    try {
        const response = await fetch('/admin/enroll_voice/save', {
            method: 'POST',
            body: formData
        });
        
        const result = await response.json();
        
        if (result.success) {
            document.getElementById('status').textContent = '🎉 ' + result.message;
            document.getElementById('status').style.color = '#10b981';
            document.getElementById('save-btn').textContent = '✅ Saved!';
            
            setTimeout(() => {
                window.location.href = '/admin/dashboard';
            }, 1500);
        } else {
            document.getElementById('status').textContent = '❌ ' + result.error;
            document.getElementById('status').style.color = '#ef4444';
            document.getElementById('save-btn').disabled = false;
            document.getElementById('save-btn').textContent = '💾 Save Voice Sample';
        }
    } catch (err) {
        document.getElementById('status').textContent = '❌ Error: ' + err.message;
        document.getElementById('status').style.color = '#ef4444';
        document.getElementById('save-btn').disabled = false;
        document.getElementById('save-btn').textContent = '💾 Save Voice Sample';
    }
}