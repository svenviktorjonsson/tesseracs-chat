// static/js/app-ui.js

export async function loadSidebarHTML(sidebarHtmlPath = '_sidebar.html', targetElementId = 'sidebar-loader-target') {
    const sidebarTarget = document.getElementById(targetElementId);
    if (!sidebarTarget) {
        console.error(`Sidebar target element with ID '${targetElementId}' not found.`);
        return false;
    }
    try {
        const response = await fetch(sidebarHtmlPath);
        if (!response.ok) {
            console.error(`Failed to fetch sidebar HTML from ${sidebarHtmlPath}. Status: ${response.status}`);
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const sidebarHTML = await response.text();
        sidebarTarget.innerHTML = sidebarHTML;
        console.log(`Sidebar HTML successfully loaded from ${sidebarHtmlPath} into #${targetElementId}`);
        return true;
    } catch (error) {
        console.error('Could not load sidebar:', error);
        sidebarTarget.innerHTML = '<p class="p-4 text-red-400">Error loading sidebar content.</p>';
        return false;
    }
}

async function handleDeleteSession(sessionId, sessionName, apiEndpoint, listElementId, chatPageBaseUrl) {
    if (!window.confirm(`Are you sure you want to delete the session "${sessionName}"? This action cannot be undone.`)) {
        return;
    }

    console.log(`Attempting to delete session: ${sessionId}`);
    try {
        const response = await fetch(`/api/sessions/${sessionId}`, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: "Failed to delete session. Server error." }));
            console.error(`Failed to delete session ${sessionId}. Status: ${response.status}`, errorData);
            alert(`Error deleting session: ${errorData.detail || response.statusText}`);
            return;
        }

        console.log(`Session ${sessionId} successfully deleted from server.`);
        
        const currentPath = window.location.pathname;
        // Ensure chatPageBaseUrl ends with a slash if not already
        const base = chatPageBaseUrl.endsWith('/') ? chatPageBaseUrl : chatPageBaseUrl + '/';
        const deletedSessionPath = `${base}${sessionId}`;

        if (currentPath === deletedSessionPath || currentPath === `${deletedSessionPath}/`) {
            window.location.href = '/'; 
        } else {
            await populateSessionList(apiEndpoint, listElementId, chatPageBaseUrl);
        }

    } catch (error) {
        console.error('Error during delete session request:', error);
        alert('An error occurred while trying to delete the session. Please check the console.');
    }
}

export async function populateSessionList(apiEndpoint = '/api/sessions', listElementId = 'session-list', chatPageBaseUrl = '/chat/') {
    const sessionListElement = document.getElementById(listElementId);
    if (!sessionListElement) {
        console.error(`Session list element with ID '${listElementId}' not found.`);
        return;
    }
    sessionListElement.innerHTML = '<li class="px-3 py-1 text-gray-400 italic text-sm">Loading sessions...</li>';

    try {
        console.log(`Workspaceing sessions from: ${apiEndpoint}`);
        const response = await fetch(apiEndpoint);

        if (!response.ok) {
            const errorText = await response.text();
            console.error(`Failed to fetch sessions from ${apiEndpoint}. Status: ${response.status}. Response: ${errorText}`);
            throw new Error(`HTTP error! status: ${response.status}. Message: ${errorText}`);
        }
        const sessions = await response.json();
        if (!Array.isArray(sessions)) {
            console.error(`Data from ${apiEndpoint} is not an array:`, sessions);
            throw new Error(`Expected an array of sessions from ${apiEndpoint}, but received other type.`);
        }

        if (sessions.length === 0) {
            sessionListElement.innerHTML = '<li class="px-3 py-1 text-gray-400 text-sm">No active sessions found.</li>';
        } else {
            sessionListElement.innerHTML = '';
            sessions.forEach(session => {
                if (!session.id || !session.name) {
                    console.warn('Session object is missing id or name:', session);
                    return;
                }

                const listItem = document.createElement('li');
                listItem.className = 'flex items-center justify-between pr-2 group hover:bg-gray-750 rounded-md'; // Added group and hover effect for item

                const link = document.createElement('a');
                const base = chatPageBaseUrl.endsWith('/') ? chatPageBaseUrl : chatPageBaseUrl + '/';
                link.href = `${base}${session.id}`; 
                
                link.className = 'block pl-3 pr-1 py-2 text-gray-300 group-hover:text-white rounded-l-md text-sm truncate flex-grow';
                
                let lastActiveDisplay = "Never";
                if (session.last_active) {
                    try {
                        lastActiveDisplay = new Date(session.last_active).toLocaleString();
                    } catch (e) {
                        console.warn("Could not parse last_active date:", session.last_active);
                        lastActiveDisplay = session.last_active; // show raw if parsing fails
                    }
                }
                link.title = `${session.name}\nLast active: ${lastActiveDisplay}`;
                link.textContent = session.name;
                
                const currentPath = window.location.pathname;
                if (currentPath === link.pathname) { 
                    link.classList.add('bg-gray-700', 'text-white', 'font-semibold');
                    listItem.classList.add('bg-gray-700'); // Also highlight the whole li item
                }


                const deleteButton = document.createElement('button');
                deleteButton.innerHTML = '&#x2715;'; 
                deleteButton.className = 'ml-2 p-1 text-gray-500 hover:text-red-400 focus:outline-none rounded-full hover:bg-gray-600 transition-colors duration-150 ease-in-out text-xs opacity-0 group-hover:opacity-100 focus:opacity-100 flex-shrink-0'; 
                deleteButton.title = `Delete session: ${session.name}`;
                
                deleteButton.onclick = (event) => {
                    event.stopPropagation(); // Prevent triggering link navigation
                    handleDeleteSession(session.id, session.name, apiEndpoint, listElementId, base);
                };

                listItem.appendChild(link);
                listItem.appendChild(deleteButton);
                sessionListElement.appendChild(listItem);
            });
        }
        console.log(`Session list successfully populated from ${apiEndpoint} with ${sessions.length} sessions.`);

    } catch (error) {
        console.error('Error populating session list:', error);
        sessionListElement.innerHTML = `<li class="px-3 py-1 text-red-400 text-sm">Could not load sessions. Error: ${error.message}</li>`;
    }
}