/**
 * AI Assistant Chat Widget Client
 * Handles real-time SocketIO communication, REST fallback, voice STT,
 * quick suggestion chips, navigation triggers, and theme sync.
 */

(function () {
    'use strict';

    // State
    let socket = null;
    let sessionId = null;
    let storageKey = null;
    let sessionStorageKey = null;

    let isRecording = false;
    let mediaRecorder = null;
    let audioChunks = [];
    let isThinking = false;
    let isLoadingHistory = false;
    let activeAudioStream = null;
    let speechRecognitionInstance = null;
    let recognitionHadResult = false;
    let recognitionStoppedByUser = false;

    // DOM Elements
    let launcher, chatWindow, closeBtn, minimizeBtn, newChatBtn, historyBtn, historyCloseBtn, historyPanel, historyList, messagesContainer, chatInput, sendBtn, micBtn, suggestionsContainer, statusDot, statusText, statusBadge;

    document.addEventListener('DOMContentLoaded', () => {
        initElements();
        initializeSessions();
        checkHealth();
        bindEvents();
        loadHistory();
    });

    function initElements() {
        launcher = document.getElementById('ai-chat-launcher');
        chatWindow = document.getElementById('ai-chat-window');
        closeBtn = document.getElementById('ai-close-btn');
        minimizeBtn = document.getElementById('ai-min-btn');
        newChatBtn = document.getElementById('ai-new-chat-btn');
        historyBtn = document.getElementById('ai-history-btn');
        historyCloseBtn = document.getElementById('ai-history-close-btn');
        historyPanel = document.getElementById('ai-history-panel');
        historyList = document.getElementById('ai-history-list');
        messagesContainer = document.getElementById('ai-messages');
        chatInput = document.getElementById('ai-input-text');
        sendBtn = document.getElementById('ai-send-btn');
        micBtn = document.getElementById('ai-mic-btn');
        suggestionsContainer = document.getElementById('ai-suggestions');
        statusDot = document.getElementById('ai-status-dot');
        statusText = document.getElementById('ai-status-text');
        statusBadge = launcher ? launcher.querySelector('.ai-badge') : null;
    }

    function newSessionId() {
        return 'sess_' + Math.random().toString(36).substring(2, 11) + '_' + Date.now();
    }

    function initializeSessions() {
        // Scope the browser-side index to the signed-in user so a shared device
        // never displays another account's conversation titles or previews.
        const root = document.getElementById('ai-chat-root');
        const userId = root && root.dataset.chatUser ? root.dataset.chatUser : 'anonymous';
        storageKey = `ai_chat_sessions_${userId}`;
        sessionStorageKey = `ai_session_id_${userId}`;
        sessionId = sessionStorage.getItem(sessionStorageKey) || newSessionId();
        sessionStorage.setItem(sessionStorageKey, sessionId);
        touchSession(sessionId);
        renderSessionList();
    }

    function readSessions() {
        try {
            const sessions = JSON.parse(localStorage.getItem(storageKey) || '[]');
            return Array.isArray(sessions) ? sessions.filter(item => item && typeof item.id === 'string') : [];
        } catch (err) {
            return [];
        }
    }

    function writeSessions(sessions) {
        try {
            localStorage.setItem(storageKey, JSON.stringify(sessions.slice(0, 30)));
        } catch (err) {
            // Private browsing or a full browser store must not break chat.
            console.debug('Could not save local chat index:', err);
        }
    }

    function touchSession(id, details = {}) {
        const sessions = readSessions();
        const existing = sessions.find(item => item.id === id) || { id, title: 'New conversation', preview: '' };
        if (details.title) existing.title = String(details.title).slice(0, 72);
        if (details.preview) existing.preview = String(details.preview).slice(0, 100);
        existing.updatedAt = Date.now();
        const updated = [existing, ...sessions.filter(item => item.id !== id)];
        writeSessions(updated);
    }

    async function renderSessionList() {
        if (!historyList) return;
        historyList.replaceChildren();
        let sessions = readSessions();
        try {
            const response = await fetch('/api/ai/sessions?limit=30', { headers: { Accept: 'application/json' } });
            const data = response.ok ? await response.json() : null;
            if (data && Array.isArray(data.sessions)) {
                const localById = new Map(sessions.map(item => [item.id, item]));
                sessions = data.sessions.map(item => {
                    const local = localById.get(item.session_id) || {};
                    return {
                        id: item.session_id,
                        title: item.title || local.title || 'Conversation',
                        preview: item.preview || local.preview || '',
                        updatedAt: item.updated_at ? new Date(item.updated_at).getTime() : local.updatedAt,
                    };
                });
                // A brand-new chat has no saved server messages yet, but it
                // should still be visible and selectable during this visit.
                const current = localById.get(sessionId);
                if (current && !sessions.some(item => item.id === sessionId)) sessions.unshift(current);
                writeSessions(sessions);
            }
        } catch (err) {
            console.debug('Could not load server conversation list:', err);
        }
        if (!sessions.length) {
            const empty = document.createElement('p');
            empty.className = 'ai-history-empty';
            empty.textContent = 'No saved conversations yet.';
            historyList.appendChild(empty);
            return;
        }
        sessions.forEach(session => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `ai-history-item${session.id === sessionId ? ' active' : ''}`;
            button.setAttribute('role', 'listitem');
            button.dataset.sessionId = session.id;
            const title = document.createElement('span');
            title.className = 'ai-history-item-title';
            title.textContent = session.title || 'New conversation';
            const detail = document.createElement('span');
            detail.className = 'ai-history-item-detail';
            const date = session.updatedAt ? new Date(session.updatedAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '';
            detail.textContent = [session.preview, date].filter(Boolean).join(' · ');
            button.append(title, detail);
            historyList.appendChild(button);
        });
    }

    function toggleHistory(forceOpen) {
        if (!historyPanel) return;
        const open = typeof forceOpen === 'boolean' ? forceOpen : historyPanel.classList.contains('hidden');
        historyPanel.classList.toggle('hidden', !open);
        historyPanel.setAttribute('aria-hidden', String(!open));
        if (historyBtn) historyBtn.setAttribute('aria-expanded', String(open));
        if (open) renderSessionList();
    }

    function startNewChat() {
        sessionId = newSessionId();
        sessionStorage.setItem(sessionStorageKey, sessionId);
        touchSession(sessionId);
        if (messagesContainer) messagesContainer.replaceChildren();
        appendMessage('assistant', 'New conversation started. How can I help with your optical shop today?');
        toggleHistory(false);
        if (chatInput) chatInput.focus();
        renderSessionList();
        if (socket && socket.connected) socket.emit('join_session', { session_id: sessionId });
    }

    async function openSession(id) {
        if (!id || id === sessionId) {
            toggleHistory(false);
            return;
        }
        sessionId = id;
        sessionStorage.setItem(sessionStorageKey, sessionId);
        toggleHistory(false);
        if (messagesContainer) messagesContainer.replaceChildren();
        await loadHistory();
        renderSessionList();
        if (socket && socket.connected) socket.emit('join_session', { session_id: sessionId });
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
            if (!res.ok) throw new Error(`Health check failed (${res.status})`);
            const data = await res.json();
                if (data.llm && data.llm.available) {
                    if (statusDot) statusDot.classList.remove('offline');
                    if (statusBadge) statusBadge.classList.remove('offline');
                    if (statusText) statusText.textContent = `Online (${data.llm.model})`;
                } else {
                    if (statusDot) statusDot.classList.add('offline');
                    if (statusBadge) statusBadge.classList.add('offline');
                    if (statusText) statusText.textContent = 'AI Offline';
            }
            // Serverless platforms use REST for chat. Only connect Socket.IO
            // after health confirms that the deployment explicitly supports it.
            if (data.realtime && data.realtime.enabled) initSocket();
        } catch (e) {
            if (statusDot) statusDot.classList.add('offline');
            if (statusBadge) statusBadge.classList.add('offline');
            if (statusText) statusText.textContent = 'AI Offline';
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
                if (launcher) launcher.setAttribute('aria-expanded', 'false');
            });
        }

        if (minimizeBtn) {
            minimizeBtn.addEventListener('click', () => {
                chatWindow.classList.add('hidden');
                if (launcher) launcher.setAttribute('aria-expanded', 'false');
            });
        }

        if (newChatBtn) newChatBtn.addEventListener('click', startNewChat);
        if (historyBtn) historyBtn.addEventListener('click', () => toggleHistory());
        if (historyCloseBtn) historyCloseBtn.addEventListener('click', () => toggleHistory(false));
        if (historyList) {
            historyList.addEventListener('click', (event) => {
                const item = event.target.closest('[data-session-id]');
                if (item) openSession(item.dataset.sessionId);
            });
        }

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && historyPanel && !historyPanel.classList.contains('hidden')) {
                event.preventDefault();
                toggleHistory(false);
                if (historyBtn) historyBtn.focus();
            }
        });

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
            if (launcher) launcher.setAttribute('aria-expanded', 'true');
            if (chatInput) chatInput.focus();
        } else {
            chatWindow.classList.add('hidden');
            if (launcher) launcher.setAttribute('aria-expanded', 'false');
        }
    }

    async function loadHistory() {
        try {
            const res = await fetch(`/api/ai/history?session_id=${encodeURIComponent(sessionId)}&limit=20`);
            if (res.ok) {
                const data = await res.json();
                if (data.messages && data.messages.length > 0) {
                    messagesContainer.replaceChildren();
                    isLoadingHistory = true;
                    try {
                        data.messages.forEach(msg => appendMessage(msg.role, msg.content));
                    } finally {
                        isLoadingHistory = false;
                    }
                } else if (messagesContainer) {
                    messagesContainer.replaceChildren();
                    appendMessage('assistant', 'New conversation started. How can I help with your optical shop today?');
                }
            }
        } catch (err) {
            console.debug('Error loading chat history:', err);
        }
    }

    async function sendMessage() {
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
            const controller = new AbortController();
            const timeout = window.setTimeout(() => controller.abort(), 45000);
            try {
                const response = await fetch('/api/ai/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify({ message: text, session_id: sessionId }),
                    signal: controller.signal,
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(data.error || `Request failed (${response.status})`);
                }
                handleAssistantResponse(data);
            } catch (err) {
                appendMessage('system', err.name === 'AbortError'
                    ? 'The assistant took too long to respond. Please try again.'
                    : (err.message || 'Error sending message. Please try again.'));
                console.error(err);
            } finally {
                window.clearTimeout(timeout);
                removeTypingIndicator();
            }
        }
    }

    function handleAssistantResponse(data) {
        if (!data) return;

        if (data.error && data.error !== 'provider_unavailable') {
            appendMessage('system', data.text || data.error);
            return;
        }

        appendMessage('assistant', data.text || '', { toolCalls: data.tool_calls || [] });

        // Automatic page navigation if requested
        if (data.navigate_to) {
            setTimeout(() => {
                window.location.href = data.navigate_to;
            }, 1200);
        }
    }

    function appendMessage(role, content, options = {}) {
        if (!messagesContainer) return;
        const msgDiv = document.createElement('div');
        msgDiv.className = `ai-msg ${role}`;

        if (role === 'user') {
            msgDiv.textContent = content;
        } else {
            // Persisted messages and model responses are always escaped before
            // adding the small, intentionally supported markdown subset.
            msgDiv.innerHTML = formatMarkdown(String(content || ''));
            (options.toolCalls || []).forEach((toolCall) => {
                const pill = document.createElement('div');
                pill.className = 'ai-tool-pill';
                const icon = document.createElement('i');
                icon.className = 'fas fa-bolt';
                pill.appendChild(icon);
                pill.append(` Executed: ${toolCall && toolCall.name ? toolCall.name : 'tool'}`);
                msgDiv.appendChild(pill);
            });
        }

        messagesContainer.appendChild(msgDiv);
        if (role === 'user') {
            const text = String(content || '').trim();
            const current = readSessions().find(item => item.id === sessionId);
            touchSession(sessionId, {
                title: current && current.title !== 'New conversation' ? current.title : (text || 'New conversation'),
                preview: text,
            });
            renderSessionList();
        }
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

    // Voice recording & STT. Native browser recognition is intentionally tried
    // first: it avoids loading a Whisper model on low-resource deployments.

    async function toggleVoiceRecording() {
        if (isRecording) {
            stopVoiceRecording();
        } else {
            startVoiceRecording();
        }
    }

    async function startVoiceRecording() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

        // Primary: native Web Speech Recognition (zero latency when supported).
        if (SpeechRecognition) {
            try {
                speechRecognitionInstance = new SpeechRecognition();
                speechRecognitionInstance.continuous = false;
                speechRecognitionInstance.interimResults = true;
                speechRecognitionInstance.lang = navigator.language || 'en-IN';

                isRecording = true;
                recognitionHadResult = false;
                recognitionStoppedByUser = false;
                if (micBtn) micBtn.classList.add('recording');

                let finalTranscript = '';

                speechRecognitionInstance.onresult = (event) => {
                    let interimTranscript = '';
                    for (let i = event.resultIndex; i < event.results.length; ++i) {
                        if (event.results[i].isFinal) {
                            finalTranscript += event.results[i][0].transcript + ' ';
                            recognitionHadResult = true;
                        } else {
                            interimTranscript += event.results[i][0].transcript;
                        }
                    }
                    if (chatInput) {
                        chatInput.value = finalTranscript || interimTranscript;
                    }
                };

                speechRecognitionInstance.onerror = (e) => {
                    console.warn('[STT] Web Speech API error:', e.error);
                    recognitionStoppedByUser = true;
                    isRecording = false;
                    if (micBtn) micBtn.classList.remove('recording');
                    if (e.error === 'not-allowed') {
                        appendMessage('system', 'Microphone permission denied. Please allow microphone access in your browser settings.');
                    } else if (e.error !== 'aborted' && e.error !== 'no-speech') {
                        appendMessage('system', 'Browser speech recognition is unavailable. You can try the microphone again to use server transcription, or type your message.');
                    }
                };

                speechRecognitionInstance.onend = () => {
                    const text = finalTranscript.trim();
                    const shouldSubmit = !recognitionStoppedByUser && recognitionHadResult && text;
                    speechRecognitionInstance = null;
                    isRecording = false;
                    if (micBtn) micBtn.classList.remove('recording');
                    if (shouldSubmit) {
                        if (chatInput) chatInput.value = text;
                        sendMessage();
                    }
                };

                speechRecognitionInstance.start();
                return;
            } catch (err) {
                console.warn('[STT] Could not start Web Speech Recognition, falling back to MediaRecorder:', err);
            }
        }

        // Secondary fallback: MediaRecorder audio capture -> server /api/ai/transcribe
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            if (!window.MediaRecorder) {
                stream.getTracks().forEach(track => track.stop());
                throw new Error('MediaRecorder is not supported by this browser.');
            }
            activeAudioStream = stream;
            mediaRecorder = new MediaRecorder(stream);
            audioChunks = [];

            mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) audioChunks.push(event.data);
            };

            mediaRecorder.onstop = async () => {
                const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                if (activeAudioStream) activeAudioStream.getTracks().forEach(track => track.stop());
                activeAudioStream = null;
                mediaRecorder = null;
                await sendAudioForTranscription(audioBlob);
            };

            mediaRecorder.start();
            isRecording = true;
            if (micBtn) micBtn.classList.add('recording');
        } catch (err) {
            console.error('Microphone access denied or unsupported:', err);
            appendMessage('system', 'Microphone not accessible. Please ensure microphone permissions are enabled.');
            stopVoiceRecording();
        }
    }

    function stopVoiceRecording() {
        if (speechRecognitionInstance) {
            recognitionStoppedByUser = true;
            try { speechRecognitionInstance.stop(); } catch (e) {}
        }
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            try { mediaRecorder.stop(); } catch (e) {}
        }
        isRecording = false;
        if (micBtn) micBtn.classList.remove('recording');
    }

    async function sendAudioForTranscription(blob) {
        showTypingIndicator();
        const formData = new FormData();
        formData.append('audio', blob, 'recording.webm');
        let timeout;

        try {
            const controller = new AbortController();
            timeout = window.setTimeout(() => controller.abort(), 45000);
            const res = await fetch('/api/ai/transcribe', {
                method: 'POST',
                body: formData,
                signal: controller.signal,
            });
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                if (data.text && data.text.trim()) {
                    if (chatInput) {
                        chatInput.value = data.text;
                        sendMessage();
                    }
                } else if (data.error) {
                    appendMessage('system', 'Voice input: ' + data.error);
                }
            } else {
                appendMessage('system', data.error || 'Voice transcription failed. Please try again or type your message.');
            }
        } catch (e) {
            console.error('STT upload error:', e);
            appendMessage('system', e.name === 'AbortError'
                ? 'Voice transcription took too long. Please try a shorter recording.'
                : 'Voice transcription failed. Please try again or type your message.');
        } finally {
            removeTypingIndicator();
            if (timeout) window.clearTimeout(timeout);
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
