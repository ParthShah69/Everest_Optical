/**
 * AI Assistant Chat Widget Client
 * Handles real-time SocketIO communication, REST fallback, voice STT,
 * quick suggestion chips, navigation triggers, and theme sync.
 */

(function () {
    'use strict';

    // State
    let socket = null;
    let sessionId = sessionStorage.getItem('ai_session_id');
    if (!sessionId) {
        sessionId = 'sess_' + Math.random().toString(36).substring(2, 11) + '_' + Date.now();
        sessionStorage.setItem('ai_session_id', sessionId);
    }

    let isRecording = false;
    let mediaRecorder = null;
    let audioChunks = [];
    let isThinking = false;

    // DOM Elements
    let launcher, chatWindow, closeBtn, minimizeBtn, messagesContainer, chatInput, sendBtn, micBtn, suggestionsContainer, statusDot, statusText;

    document.addEventListener('DOMContentLoaded', () => {
        initElements();
        initSocket();
        checkHealth();
        bindEvents();
        loadHistory();
    });

    function initElements() {
        launcher = document.getElementById('ai-chat-launcher');
        chatWindow = document.getElementById('ai-chat-window');
        closeBtn = document.getElementById('ai-close-btn');
        minimizeBtn = document.getElementById('ai-min-btn');
        messagesContainer = document.getElementById('ai-messages');
        chatInput = document.getElementById('ai-input-text');
        sendBtn = document.getElementById('ai-send-btn');
        micBtn = document.getElementById('ai-mic-btn');
        suggestionsContainer = document.getElementById('ai-suggestions');
        statusDot = document.getElementById('ai-status-dot');
        statusText = document.getElementById('ai-status-text');
    }

    function initSocket() {
        if (typeof io !== 'undefined') {
            try {
                socket = io();
                socket.on('connect', () => {
                    if (statusDot) {
                        statusDot.classList.remove('offline');
                        statusText.textContent = 'Online';
                    }
                    socket.emit('join_session', { session_id: sessionId });
                });

                socket.on('disconnect', () => {
                    if (statusDot) {
                        statusDot.classList.add('offline');
                        statusText.textContent = 'Disconnected';
                    }
                });

                socket.on('chat_typing', () => {
                    showTypingIndicator();
                });

                socket.on('chat_response', (data) => {
                    removeTypingIndicator();
                    handleAssistantResponse(data);
                });

                socket.on('chat_error', (data) => {
                    removeTypingIndicator();
                    appendMessage('system', data.error || 'An error occurred.');
                });
            } catch (err) {
                console.warn('SocketIO connection error, fallback to REST API:', err);
                socket = null;
            }
        }
    }

    async function checkHealth() {
        try {
            const res = await fetch('/api/ai/health');
            if (res.ok) {
                const data = await res.json();
                if (data.llm && data.llm.available) {
                    if (statusDot) statusDot.classList.remove('offline');
                    if (statusText) statusText.textContent = `Online (${data.llm.model})`;
                } else {
                    if (statusDot) statusDot.classList.add('offline');
                    if (statusText) statusText.textContent = 'LLM Offline';
                }
            }
        } catch (e) {
            console.debug('AI Health check failed:', e);
        }
    }

    function bindEvents() {
        if (launcher) {
            launcher.addEventListener('click', () => {
                toggleChatWindow();
            });
        }

        if (closeBtn) {
            closeBtn.addEventListener('click', () => {
                chatWindow.classList.add('hidden');
            });
        }

        if (minimizeBtn) {
            minimizeBtn.addEventListener('click', () => {
                chatWindow.classList.add('hidden');
            });
        }

        if (sendBtn) {
            sendBtn.addEventListener('click', () => {
                sendMessage();
            });
        }

        if (chatInput) {
            chatInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    sendMessage();
                }
            });
        }

        if (micBtn) {
            micBtn.addEventListener('click', () => {
                toggleVoiceRecording();
            });
        }

        if (suggestionsContainer) {
            suggestionsContainer.addEventListener('click', (e) => {
                const chip = e.target.closest('.ai-chip');
                if (chip) {
                    const prompt = chip.getAttribute('data-prompt') || chip.textContent;
                    chatInput.value = prompt;
                    sendMessage();
                }
            });
        }
    }

    function toggleChatWindow() {
        if (!chatWindow) return;
        const isHidden = chatWindow.classList.contains('hidden');
        if (isHidden) {
            chatWindow.classList.remove('hidden');
            if (chatInput) chatInput.focus();
        } else {
            chatWindow.classList.add('hidden');
        }
    }

    async function loadHistory() {
        try {
            const res = await fetch(`/api/ai/history?session_id=${encodeURIComponent(sessionId)}&limit=20`);
            if (res.ok) {
                const data = await res.json();
                if (data.messages && data.messages.length > 0) {
                    messagesContainer.innerHTML = '';
                    data.messages.forEach(msg => {
                        appendMessage(msg.role, msg.content);
                    });
                }
            }
        } catch (err) {
            console.debug('Error loading chat history:', err);
        }
    }

    function sendMessage() {
        const text = (chatInput.value || '').trim();
        if (!text || isThinking) return;

        appendMessage('user', text);
        chatInput.value = '';
        showTypingIndicator();

        if (socket && socket.connected) {
            socket.emit('chat_message', {
                message: text,
                session_id: sessionId
            });
        } else {
            // REST Fallback
            fetch('/api/ai/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text, session_id: sessionId })
            })
                .then(r => r.json())
                .then(data => {
                    removeTypingIndicator();
                    handleAssistantResponse(data);
                })
                .catch(err => {
                    removeTypingIndicator();
                    appendMessage('system', 'Error sending message. Please try again.');
                    console.error(err);
                });
        }
    }

    function handleAssistantResponse(data) {
        if (!data) return;

        if (data.error && data.error !== 'ollama_unavailable') {
            appendMessage('system', data.text || data.error);
            return;
        }

        let messageText = data.text || '';
        let toolPills = '';

        if (data.tool_calls && data.tool_calls.length > 0) {
            toolPills = data.tool_calls.map(tc => `<div class="ai-tool-pill"><i class="fas fa-bolt"></i> Executed: ${tc.name || 'tool'}</div>`).join('');
        }

        appendMessage('assistant', toolPills + formatMarkdown(messageText));

        // Automatic page navigation if requested
        if (data.navigate_to) {
            setTimeout(() => {
                window.location.href = data.navigate_to;
            }, 1200);
        }
    }

    function appendMessage(role, content) {
        if (!messagesContainer) return;
        const msgDiv = document.createElement('div');
        msgDiv.className = `ai-msg ${role}`;

        if (role === 'assistant' || role === 'system') {
            msgDiv.innerHTML = content;
        } else {
            msgDiv.textContent = content;
        }

        messagesContainer.appendChild(msgDiv);
        scrollToBottom();
    }

    function showTypingIndicator() {
        if (isThinking || !messagesContainer) return;
        isThinking = true;
        const typingDiv = document.createElement('div');
        typingDiv.id = 'ai-typing-indicator';
        typingDiv.className = 'ai-typing';
        typingDiv.innerHTML = '<span class="ai-dot"></span><span class="ai-dot"></span><span class="ai-dot"></span>';
        messagesContainer.appendChild(typingDiv);
        scrollToBottom();
    }

    function removeTypingIndicator() {
        isThinking = false;
        const ind = document.getElementById('ai-typing-indicator');
        if (ind) ind.remove();
    }

    function scrollToBottom() {
        if (messagesContainer) {
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
    }

    // Voice recording & STT
    async function toggleVoiceRecording() {
        if (isRecording) {
            stopVoiceRecording();
        } else {
            startVoiceRecording();
        }
    }

    async function startVoiceRecording() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            mediaRecorder = new MediaRecorder(stream);
            audioChunks = [];

            mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) audioChunks.push(event.data);
            };

            mediaRecorder.onstop = async () => {
                const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                stream.getTracks().forEach(track => track.stop());
                await sendAudioForTranscription(audioBlob);
            };

            mediaRecorder.start();
            isRecording = true;
            if (micBtn) micBtn.classList.add('recording');
        } catch (err) {
            console.error('Microphone access denied or unsupported:', err);
            // Fallback: Web Speech Recognition API if available
            startWebSpeechFallback();
        }
    }

    function stopVoiceRecording() {
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            mediaRecorder.stop();
        }
        isRecording = false;
        if (micBtn) micBtn.classList.remove('recording');
    }

    function startWebSpeechFallback() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            alert('Voice recording requires microphone permissions or a supported browser.');
            return;
        }

        const recognition = new SpeechRecognition();
        recognition.lang = 'hi-IN'; // Multi-language friendly for Indian context
        recognition.interimResults = false;

        if (micBtn) micBtn.classList.add('recording');

        recognition.onresult = (event) => {
            const transcript = event.results[0][0].transcript;
            if (chatInput) {
                chatInput.value = transcript;
                sendMessage();
            }
        };

        recognition.onerror = (e) => {
            console.warn('Web Speech API error:', e);
            if (micBtn) micBtn.classList.remove('recording');
        };

        recognition.onend = () => {
            if (micBtn) micBtn.classList.remove('recording');
        };

        recognition.start();
    }

    async function sendAudioForTranscription(blob) {
        showTypingIndicator();
        const formData = new FormData();
        formData.append('audio', blob, 'recording.webm');

        try {
            const res = await fetch('/api/ai/transcribe', {
                method: 'POST',
                body: formData
            });

            removeTypingIndicator();
            if (res.ok) {
                const data = await res.json();
                if (data.text && data.text.trim()) {
                    if (chatInput) {
                        chatInput.value = data.text;
                        sendMessage();
                    }
                } else if (data.error) {
                    appendMessage('system', 'Voice input: ' + data.error);
                }
            }
        } catch (e) {
            removeTypingIndicator();
            console.error('STT upload error:', e);
        }
    }

    // Basic markdown formatter for LLM text responses
    function formatMarkdown(text) {
        if (!text) return '';
        let escaped = text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');

        // Bold
        escaped = escaped.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        // Italic
        escaped = escaped.replace(/\*(.*?)\*/g, '<em>$1</em>');
        // Inline code
        escaped = escaped.replace(/`([^`]+)`/g, '<code>$1</code>');
        // Line breaks
        escaped = escaped.replace(/\n/g, '<br>');

        return escaped;
    }

})();
