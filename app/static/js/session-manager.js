// --- Start: session-manager.js ---

// Helper functions for storing and retrieving passcodes
function savePasscode(sessionId, passcode) {
    try {
        const passcodes = JSON.parse(localStorage.getItem('mpld3_passcodes')) || {};
        passcodes[sessionId] = passcode;
        localStorage.setItem('mpld3_passcodes', JSON.stringify(passcodes));
    } catch (e) {
        console.error("Could not save passcode to localStorage:", e);
    }
}

function getPasscode(sessionId) {
    try {
        const passcodes = JSON.parse(localStorage.getItem('mpld3_passcodes')) || {};
        return passcodes[sessionId] || null;
    } catch (e) {
        console.error("Could not retrieve passcode from localStorage:", e);
        return null;
    }
}

// Helper function to create a list item for the "Joinable" list
function createJoinableSessionListItem(session) {
    const listItem = document.createElement('li');
    listItem.className = 'flex items-center justify-between p-2 bg-gray-100 rounded-md';
    listItem.dataset.sessionId = session.id;

    const nameSpan = document.createElement('span');
    nameSpan.className = 'text-sm text-gray-800 truncate';
    nameSpan.textContent = session.name || `Session ${session.id.substring(0, 8)}`;
    nameSpan.title = session.name;

    const actionButton = document.createElement('button');
    actionButton.className = 'px-3 py-1 text-white text-xs font-semibold rounded-md transition-colors';

    if (session.is_member) {
        actionButton.textContent = 'Open';
        actionButton.classList.add('bg-blue-500', 'hover:bg-blue-600');
        actionButton.onclick = () => { window.location.href = `/chat/${session.id}`; };
    } else {
        actionButton.textContent = 'Join';
        if (session.access_level === 'protected') {
            actionButton.classList.add('bg-orange-500', 'hover:bg-orange-600');
        } else {
            actionButton.classList.add('bg-green-500', 'hover:bg-green-600');
        }
        actionButton.onclick = async () => {
            let passcode = getPasscode(session.id);
            if (session.access_level === 'protected' && !passcode) {
                passcode = prompt(`Session "${session.name}" is protected. Please enter the passcode:`);
                if (passcode === null) return;
            }
            const attemptJoin = async (passcodeToTry) => {
                actionButton.textContent = 'Joining...';
                actionButton.disabled = true;
                try {
                    const joinResponse = await fetch(`/api/sessions/${session.id}/join`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window.csrfTokenRaw },
                        body: JSON.stringify({ passcode: passcodeToTry })
                    });
                    const result = await joinResponse.json();
                    if (joinResponse.ok && result.redirect_url) {
                        if (session.access_level === 'protected' && passcodeToTry) {
                            savePasscode(session.id, passcodeToTry);
                        }
                        window.location.href = result.redirect_url;
                    } else {
                        if (joinResponse.status === 403) {
                            const newPasscode = prompt(`Incorrect passcode for "${session.name}". Please try again:`);
                            if (newPasscode !== null) await attemptJoin(newPasscode);
                            else {
                                actionButton.textContent = 'Join';
                                actionButton.disabled = false;
                            }
                        } else {
                            alert('Failed to join: ' + (result.detail || 'Unknown error'));
                            actionButton.textContent = 'Join';
                            actionButton.disabled = false;
                        }
                    }
                } catch (error) {
                    alert('An error occurred.');
                    actionButton.textContent = 'Join';
                    actionButton.disabled = false;
                }
            };
            await attemptJoin(passcode);
        };
    }
    listItem.appendChild(nameSpan);
    listItem.appendChild(actionButton);
    return listItem;
}

export function initializeDashboard() {
    const hostTabButton = document.getElementById('host-tab-button');
    const joinTabButton = document.getElementById('join-tab-button');
    const hostSessionView = document.getElementById('host-session-view');
    const joinSessionView = document.getElementById('join-session-view');
    const sessionNameInput = document.getElementById('session-name');

    if (sessionNameInput) {
        // This is the new fix: Clear the value, and remove the 'readonly'
        // attribute only when the user focuses the input.
        sessionNameInput.value = '';
        sessionNameInput.addEventListener('focus', () => {
            sessionNameInput.removeAttribute('readonly');
        }, { once: true });
    }

    if (!hostTabButton || !joinTabButton || !hostSessionView || !joinSessionView) return;

    const setActiveTab = (tabName) => {
        if (tabName === 'host') {
            hostTabButton.classList.add('text-blue-600', 'border-blue-500');
            hostTabButton.classList.remove('text-gray-500', 'hover:text-gray-700', 'hover:border-gray-300');
            joinTabButton.classList.add('text-gray-500', 'hover:text-gray-700', 'hover:border-gray-300');
            joinTabButton.classList.remove('text-blue-600', 'border-blue-500');
            hostSessionView.classList.remove('hidden');
            joinSessionView.classList.add('hidden');
        } else {
            joinTabButton.classList.add('text-blue-600', 'border-blue-500');
            joinTabButton.classList.remove('text-gray-500', 'hover:text-gray-700', 'hover:border-gray-300');
            hostTabButton.classList.add('text-gray-500', 'hover:text-gray-700', 'hover:border-gray-300');
            hostTabButton.classList.remove('text-blue-600', 'border-blue-500');
            joinSessionView.classList.remove('hidden');
            hostSessionView.classList.add('hidden');
        }
    };

    hostTabButton.addEventListener('click', () => setActiveTab('host'));
    joinTabButton.addEventListener('click', () => setActiveTab('join'));

    handleHostSessionForm();
}

export function handleHostSessionForm() {
    const form = document.getElementById('host-session-form');
    const accessSelect = document.getElementById('session-access');
    const passcodeGroup = document.getElementById('passcode-group');
    const passcode_input = document.getElementById('session-passcode');
    const button = document.getElementById('host-session-button');

    if (!form || !accessSelect || !passcodeGroup || !passcode_input || !button) return;

    // --- Start of Fix ---
    // Make the password field editable on focus to trick autofill
    passcode_input.addEventListener('focus', () => {
        passcode_input.removeAttribute('readonly');
    }, { once: true });
    // --- End of Fix ---

    accessSelect.addEventListener('change', () => {
        if (accessSelect.value === 'protected') {
            passcodeGroup.classList.remove('hidden');
            passcode_input.required = true;
        } else {
            passcodeGroup.classList.add('hidden');
            passcode_input.required = false;
        }
    });

    button.addEventListener('click', async (event) => {
        event.preventDefault();
        
        if (!form.checkValidity()) {
            form.reportValidity();
            return;
        }

        button.disabled = true;
        button.textContent = 'Hosting...';

        const formData = new FormData(form);
        const payload = Object.fromEntries(formData.entries());

        try {
            const response = await fetch('/sessions/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window.csrfTokenRaw },
                body: JSON.stringify(payload)
            });

            const result = await response.json();
            if (response.ok) {
                window.location.href = `/chat/${result.id}`;
            } else {
                alert(`Error: ${result.detail || 'Could not host session.'}`);
                button.disabled = false;
                button.textContent = 'Host New Session';
            }
        } catch (error) {
            alert('A network error occurred. Please try again.');
            button.disabled = false;
            button.textContent = 'Host New Session';
        }
    });
}

export function handleSessionSettings() {
    const settingsButton = document.getElementById('session-settings-button');
    const chatView = document.getElementById('chat-view');
    const settingsView = document.getElementById('session-settings-view');
    const backToChatButton = document.getElementById('back-to-chat-button');
    const sessionNameForDelete = document.getElementById('session-name-for-delete');
    const deleteConfirmationInput = document.getElementById('delete-confirmation');
    const deleteSessionButton = document.getElementById('delete-session-button');
    
    if (!settingsButton || !chatView || !settingsView || !backToChatButton || !sessionNameForDelete || !deleteConfirmationInput || !deleteSessionButton) return;

    const sessionId = window.location.pathname.split('/')[2];
    const currentUser = window.currentUserInfo;
    const sessionData = window.currentSessionData;
    
    if (currentUser && sessionData && currentUser.id === sessionData.host_user_id) {
        settingsButton.classList.remove('hidden');
    }

    settingsButton.addEventListener('click', () => {
        chatView.classList.add('hidden');
        settingsView.classList.remove('hidden');
        sessionNameForDelete.textContent = sessionData.name;
        deleteConfirmationInput.value = '';
        deleteSessionButton.disabled = true;
    });

    backToChatButton.addEventListener('click', () => {
        settingsView.classList.add('hidden');
        chatView.classList.remove('hidden');
    });

    deleteConfirmationInput.addEventListener('input', () => {
        deleteSessionButton.disabled = deleteConfirmationInput.value !== sessionData.name;
    });

    deleteSessionButton.addEventListener('click', async () => {
        try {
            const response = await fetch(`/api/sessions/${sessionId}/delete-by-host`, {
                method: 'DELETE',
                headers: { 'X-CSRF-Token': window.csrfTokenRaw }
            });
            if (response.ok) {
                alert("Session deleted successfully.");
                window.location.href = '/';
            } else {
                const error = await response.json();
                alert(`Error: ${error.detail}`);
            }
        } catch (err) {
            alert("An error occurred during deletion.");
        }
    });
}

export async function populateJoinableSessionList(apiEndpoint = '/api/sessions?scope=joinable', listElementId = 'joinable-session-list') {
    const sessionListElement = document.getElementById(listElementId);
    if (!sessionListElement) return;
    sessionListElement.innerHTML = '<li class="px-3 py-1 text-gray-500 italic text-sm">Loading...</li>';

    try {
        const response = await fetch(apiEndpoint);
        if (!response.ok) throw new Error('Failed to fetch sessions');
        
        const sessions = await response.json();
        if (sessions.length === 0) {
            sessionListElement.innerHTML = '<li class="px-3 py-1 text-gray-500 text-sm italic">No public sessions available.</li>';
        } else {
            sessionListElement.innerHTML = '';
            sessions.forEach(session => {
                sessionListElement.appendChild(createJoinableSessionListItem(session));
            });
        }
    } catch (error) {
        sessionListElement.innerHTML = `<li class="px-3 py-1 text-red-500 text-sm">Could not load sessions.</li>`;
    }
}

export async function updateAndDisplayParticipants(participants) {
    const participantList = document.getElementById('participant-list');
    if (!participantList) return;

    window.isAiConfigured = false;

    if (!participants) {
        const sessionId = window.location.pathname.split('/')[2];
        if (!sessionId) return;
        try {
            const response = await fetch(`/api/sessions/${sessionId}/participants`);
            if (!response.ok) {
                participantList.innerHTML = '<li class="text-red-500 text-sm">Error loading participants.</li>';
                return;
            }
            participants = await response.json();
        } catch (error) {
            participantList.innerHTML = '<li class="text-red-500 text-sm">Could not load participants.</li>';
            return;
        }
    }

    try {
        const settingsResponse = await fetch('/api/me/llm-settings');
        if (settingsResponse.ok) {
            const settings = await settingsResponse.json();
            if (settings.is_llm_ready) {
                window.isAiConfigured = true;
            }
        }
    } catch (error) {
        console.error("Could not check LLM configuration:", error);
    }
    
    window.participantInfo = {};
    participants.forEach(p => {
        window.participantInfo[p.id] = { initials: p.initials, color: p.color, name: p.name };
        if (window.currentUserInfo && window.currentUserInfo.id === p.id) {
            window.currentUserInfo.color = p.color;
        }
    });

    const sessionData = window.currentSessionData;
    const hostId = sessionData ? sessionData.host_user_id : null;

    participantList.innerHTML = '';

    const aiColor = '#E0F2FE'; // Use sky-100 as the AI's consistent base color
    window.participantInfo['AI'] = { initials: 'AI', name: 'AI Assistant', color: aiColor };

    const ai_li = document.createElement('li');
    ai_li.className = 'flex items-center space-x-2';
    ai_li.id = `participant-AI`;

    const ai_avatar = document.createElement('div');
    ai_avatar.className = 'w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold text-gray-700';
    ai_avatar.style.backgroundColor = aiColor;
    ai_avatar.textContent = 'AI';
    
    const ai_nameSpan = document.createElement('span');
    ai_nameSpan.className = 'text-sm italic participant-name-span text-gray-600';
    ai_nameSpan.textContent = 'AI Assistant';

    if (!window.isAiConfigured) {
        ai_li.classList.add('opacity-50');
        ai_nameSpan.title = "AI is not configured for your account. Go to User Settings to enable it.";
    }

    ai_li.appendChild(ai_avatar);
    ai_li.appendChild(ai_nameSpan);
    participantList.appendChild(ai_li);

    participants.forEach(user => {
        const li = document.createElement('li');
        li.className = 'flex items-center space-x-2';
        li.id = `participant-${user.id}`;
        
        const avatar = document.createElement('div');
        avatar.className = 'w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold text-gray-700';
        avatar.style.backgroundColor = user.color;
        avatar.textContent = user.initials;
        
        const nameSpan = document.createElement('span');
        nameSpan.className = 'text-sm text-gray-600 participant-name-span';
        nameSpan.textContent = user.name;
        
        if (user.id === hostId) {
            const hostLabel = document.createElement('span');
            hostLabel.className = 'text-xs text-gray-500 font-semibold ml-1';
            hostLabel.textContent = '(Host)';
            nameSpan.appendChild(hostLabel);
        }
        
        li.appendChild(avatar);
        li.appendChild(nameSpan);
        participantList.appendChild(li);
    });
}

export function connectToLobbySocket() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/lobby`;
    const lobbyWs = new WebSocket(wsUrl);

    lobbyWs.onmessage = (event) => {
        try {
            const message = JSON.parse(event.data);
            const sessionListElement = document.getElementById('joinable-session-list');
            if (!sessionListElement) return;

            if (message.type === 'new_public_session') {
                const session = message.payload;
                const placeholder = sessionListElement.querySelector('li.italic');
                if (placeholder) placeholder.remove();
                
                if (!sessionListElement.querySelector(`[data-session-id="${session.id}"]`)) {
                    const newItem = createJoinableSessionListItem(session);
                    sessionListElement.prepend(newItem);
                }
            } else if (message.type === 'session_deleted') {
                const itemToRemove = sessionListElement.querySelector(`[data-session-id="${message.payload.session_id}"]`);
                if (itemToRemove) itemToRemove.remove();
            }
        } catch (e) { console.error("Error processing lobby message:", e); }
    };

    lobbyWs.onclose = () => {
        console.log("Lobby WebSocket closed. Reconnecting in 5 seconds...");
        setTimeout(connectToLobbySocket, 5000);
    };
    lobbyWs.onerror = (err) => {
        console.error("Lobby WebSocket error:", err);
    };
}
// --- End: session-manager.js ---