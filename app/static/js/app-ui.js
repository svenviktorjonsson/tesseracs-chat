window.isNavigatingAway = false;


export async function loadSidebarHTML(sidebarHtmlPath = '/static/_sidebar.html', targetElementId = 'sidebar-loader-target') {
    const sidebarTarget = document.getElementById(targetElementId);
    if (!sidebarTarget) {
        console.error(`app-ui.js: Sidebar target element with ID '${targetElementId}' not found.`);
        return false;
    }
    try {
        const response = await fetch(sidebarHtmlPath);
        if (!response.ok) {
            console.error(`app-ui.js: Failed to fetch sidebar HTML from ${sidebarHtmlPath}. Status: ${response.status}`);
            sidebarTarget.innerHTML = `<p class="p-4 text-red-400">Error loading sidebar (status: ${response.status}).</p>`;
            return false;
        }
        const sidebarHTML = await response.text();
        sidebarTarget.innerHTML = sidebarHTML;
        return true;
    } catch (error) {
        console.error('app-ui.js: Could not load sidebar due to an error:', error);
        sidebarTarget.innerHTML = '<p class="p-4 text-red-400">Error loading sidebar content (exception).</p>';
        return false;
    }
}

export async function populateSessionList(apiEndpoint = '/api/sessions', listElementId = 'session-list', chatPageBaseUrl = '/chat/') {
    const sessionListElement = document.getElementById(listElementId);
    const filterInput = document.getElementById('session-filter-input');
    if (!sessionListElement || !filterInput) return;

    sessionListElement.innerHTML = '<li class="px-3 py-1 text-gray-400 italic text-sm">Loading sessions...</li>';

    try {
        const response = await fetch(apiEndpoint);
        if (!response.ok) throw new Error(`Server responded with status ${response.status}`);
        
        const sessions = await response.json();
        sessionListElement.innerHTML = '';

        if (sessions.length === 0) {
            sessionListElement.innerHTML = '<li class="px-3 py-1 text-gray-400 text-sm italic">No sessions in your history.</li>';
        } else {
            const currentUser = window.currentUserInfo;
            const displayedSessions = sessions.slice(0, 10);
            
            window.sessionHistory = sessions;

            displayedSessions.forEach(session => {
                const isHost = currentUser && currentUser.id === session.host_user_id;
                
                const listItem = document.createElement('li');
                listItem.className = 'flex items-center justify-between pr-2 group rounded-md';
                listItem.dataset.sessionName = (session.name || '').toLowerCase();

                const link = document.createElement('a');
                link.className = 'flex items-center pl-3 pr-1 py-2 text-gray-300 rounded-l-md text-sm truncate flex-grow';
                
                if (!session.is_active) {
                    link.classList.add('text-gray-500', 'italic', 'cursor-not-allowed');
                    link.innerHTML = `<span>[Deleted] ${session.name}</span>`;
                } else {
                    const base = chatPageBaseUrl.endsWith('/') ? chatPageBaseUrl : chatPageBaseUrl + '/';
                    link.href = `${base}${session.id}`;
                    const isActive = window.location.pathname.startsWith(link.pathname);
                    link.classList.add('group-hover:text-white', 'hover:text-white');
                    if (isActive) {
                        listItem.classList.add('bg-gray-700');
                        link.classList.add('font-semibold');
                    }
                    link.textContent = session.name;
                    if (isHost) {
                        const hostBadge = document.createElement('span');
                        hostBadge.className = 'ml-2 text-xs font-semibold bg-yellow-500 text-yellow-900 px-1.5 py-0.5 rounded-full';
                        hostBadge.textContent = 'Host';
                        link.appendChild(hostBadge);
                    }
                }
                
                const deleteButton = document.createElement('button');
                deleteButton.innerHTML = '&#x2715;';
                deleteButton.className = 'ml-2 p-1 text-gray-500 hover:text-red-400 focus:outline-none rounded-full hover:bg-gray-600 opacity-0 group-hover:opacity-100 flex-shrink-0';
                deleteButton.title = isHost ? 'Hide from history' : 'Leave session';
                deleteButton.onclick = (e) => { e.preventDefault(); e.stopPropagation(); handleDeleteSession(session, isHost, listItem); };

                listItem.appendChild(link);
                listItem.appendChild(deleteButton);
                sessionListElement.appendChild(listItem);
            });

            filterInput.addEventListener('input', () => {
                const filterText = filterInput.value.toLowerCase();
                sessionListElement.querySelectorAll('li').forEach(item => {
                    item.style.display = item.dataset.sessionName.includes(filterText) ? 'flex' : 'none';
                });
            });
        }
    } catch (error) {
        sessionListElement.innerHTML = `<li class="px-3 py-1 text-red-400 text-sm">Could not load sessions.</li>`;
    }
}

export async function handleDeleteSession(session, isHost, listItem) {
    const sessionName = session.name || 'Unnamed Session';
    
    // Determine the correct confirmation text based on the session's state
    let actionText = '';
    if (!session.is_active) {
        actionText = `Are you sure you want to remove "[Deleted] ${sessionName}" from your history?`;
    } else if (isHost) {
        actionText = `You are the host. Hiding this session will NOT delete it. You can find it again in your settings. Continue?`;
    } else {
        actionText = `Are you sure you want to leave the session "${sessionName}"? This action cannot be undone.`;
    }

    if (!window.confirm(actionText)) {
        return;
    }

    // This is the primary fix: We now ALWAYS send a request to the backend.
    try {
        const response = await fetch(`/api/sessions/${session.id}`, {
            method: 'DELETE',
            headers: { 'X-CSRF-Token': window.csrfTokenRaw }
        });

        if (response.status === 204) {
            // The action was successful, now update the UI.
            if (window.location.pathname.startsWith(`/chat/${session.id}`)) {
                window.location.href = '/'; 
            } else {
                listItem.remove();
            }
        } else {
            const errorData = await response.json();
            alert(`Error: ${errorData.detail}`);
        }
    } catch (error) {
        alert('An error occurred while processing your request.');
    }
}

