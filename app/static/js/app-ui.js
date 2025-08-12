// static/js/app-ui.js

function setupSidebarCSRF() {
    const sidebarCsrfField = document.getElementById('sidebar_csrf_token');
    const newChatButton = document.getElementById('new-chat-button-sidebar'); // For disabling/enabling

    // First, ensure the critical CSRF input field exists.
    if (!sidebarCsrfField) {
        console.error("app-ui.js: CRITICAL - CSRF token input field 'sidebar_csrf_token' NOT FOUND in the sidebar HTML. 'New Chat' functionality will be impaired or fail.");
        if (newChatButton) {
            newChatButton.disabled = true;
            newChatButton.title = "Error: Security component missing. Cannot create new chat.";
        }
        // alert("A critical security component for creating new chats is missing. Please refresh or contact support if this persists.");
        return; // Stop execution if the field isn't there; nothing more to do.
    }

    // If the field exists, attempt to populate it.
    if (window.csrfTokenRaw && 
        typeof window.csrfTokenRaw === 'string' && 
        window.csrfTokenRaw !== "%%CSRF_TOKEN_RAW%%" && 
        window.csrfTokenRaw.trim() !== "") {
        
        sidebarCsrfField.value = window.csrfTokenRaw;
        console.log("app-ui.js: Sidebar CSRF token field ('sidebar_csrf_token') populated successfully.");
        if (newChatButton) {
            newChatButton.disabled = false; // Ensure button is enabled as token is available
            newChatButton.title = "Start a new chat session"; // Reset title
        }
    } else {
        // The field exists, but the global CSRF token is invalid or missing.
        console.warn("app-ui.js: window.csrfTokenRaw is invalid, placeholder, or empty. CSRF token for sidebar form cannot be populated. Value of window.csrfTokenRaw:", window.csrfTokenRaw);
        sidebarCsrfField.value = ""; // Ensure the field is empty if the token is bad
        if (newChatButton) {
            newChatButton.disabled = true; // Disable button if token is missing/invalid
            newChatButton.title = "New Chat disabled: Security token missing or invalid. Please refresh the page.";
        }
        // alert("A security token required for creating a new chat is missing or invalid. Please try refreshing the page. If the issue persists, contact support.");
    }
}

/**
 * Loads HTML content (like a sidebar) from a given path into a target element.
 * Also sets up CSRF token for forms within the loaded HTML and handles
 * the "New Chat" form submission via AJAX.
 * @param {string} sidebarHtmlPath - Path to the HTML file to load (e.g., '/static/_sidebar.html').
 * @param {string} targetElementId - ID of the DOM element to load the HTML into.
 * @returns {Promise<boolean>} - True if successful, false otherwise.
 */
export async function loadSidebarHTML(sidebarHtmlPath = '/static/_sidebar.html', targetElementId = 'sidebar-loader-target') {
    const sidebarTarget = document.getElementById(targetElementId);
    if (!sidebarTarget) {
        console.error(`app-ui.js: Sidebar target element with ID '${targetElementId}' not found.`);
        return false;
    }
    try {
        const response = await fetch(sidebarHtmlPath); // Fetch the static _sidebar.html
        if (!response.ok) {
            console.error(`app-ui.js: Failed to fetch sidebar HTML from ${sidebarHtmlPath}. Status: ${response.status}`);
            sidebarTarget.innerHTML = `<p class="p-4 text-red-400">Error loading sidebar (status: ${response.status}).</p>`;
            return false;
        }
        const sidebarHTML = await response.text();
        sidebarTarget.innerHTML = sidebarHTML;
        console.log(`app-ui.js: Sidebar HTML successfully loaded from ${sidebarHtmlPath} into #${targetElementId}`);

        // Setup CSRF for the loaded sidebar form AFTER injecting HTML
        // This function (setupSidebarCSRF) should populate the #sidebar_csrf_token input field
        setupSidebarCSRF();

        // --- MODIFIED PART: Handle "New Chat" form submission with AJAX ---
        const newChatFormSidebar = document.getElementById('new-chat-form-sidebar');
        const sidebarCsrfTokenInput = document.getElementById('sidebar_csrf_token'); // The hidden input
        const newChatButton = document.getElementById('new-chat-button-sidebar');

        if (newChatFormSidebar && sidebarCsrfTokenInput && newChatButton) {
            newChatFormSidebar.addEventListener('submit', async function(event) {
                event.preventDefault(); // Prevent default HTML form submission

                const rawCsrfToken = sidebarCsrfTokenInput.value;

                if (!rawCsrfToken || rawCsrfToken === "%%CSRF_TOKEN_RAW%%" || rawCsrfToken.trim() === "") {
                    alert("A security token for creating new chats is missing or invalid. Please refresh the page.");
                    console.error("New Chat submission: CSRF token from sidebar_csrf_token input is missing or placeholder:", rawCsrfToken);
                    return;
                }

                newChatButton.disabled = true;
                newChatButton.innerHTML = `
                    <svg class="animate-spin h-5 w-5 mr-2 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    Creating...`;

                try {
                    const fetchResponse = await fetch('/sessions/create', { // The form's action attribute
                        method: 'POST', // The form's method attribute
                        headers: {
                            'X-CSRF-Token': rawCsrfToken // Send the raw token in the header
                            // Content-Type is not strictly necessary for an empty POST body,
                            // but if issues arise, 'application/x-www-form-urlencoded' could be added.
                            // However, the server endpoint /sessions/create doesn't expect other form data.
                        }
                        // No body is sent, as the original form only contained the CSRF token for this purpose.
                    });

                    if (fetchResponse.ok) {
                        // The server responds with HTTP 303 See Other,
                        // fetch with redirect: 'follow' (default) should handle this.
                        // The browser will navigate to the URL specified in the Location header.
                        if (fetchResponse.redirected) {
                            window.location.href = fetchResponse.url;
                        } else {
                            // If for some reason it wasn't redirected (e.g. server didn't send 303 or Location)
                            // or if the status is OK but not a redirect (e.g. 200 with JSON URL)
                            // We might need to manually parse a redirect URL if the server sends one in JSON.
                            // For now, assume the 303 redirect will be followed.
                            // If the server sends Location header, browser handles it.
                            // If not, a page reload or navigation to '/' might be a fallback.
                            console.warn("New chat creation was successful, but no redirect occurred client-side. Server status:", fetchResponse.status);
                            // Attempt to get location header if browser didn't auto-redirect
                            const locationHeader = fetchResponse.headers.get('Location');
                            if (locationHeader) {
                                window.location.href = locationHeader;
                            } else {
                                window.location.href = '/'; // Fallback to home/session choice
                            }
                        }
                    } else {
                        const errorData = await fetchResponse.json().catch(() => ({ 
                            detail: `Failed to create new chat. Server responded with status: ${fetchResponse.status}` 
                        }));
                        console.error('Failed to create new chat session:', errorData.detail);
                        alert(`Error creating new chat: ${errorData.detail}`);
                        newChatButton.disabled = false;
                        newChatButton.innerHTML = `
                            <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6"></path></svg>
                            New Chat`;
                    }
                } catch (error) {
                    console.error('Network error or other issue trying to create new chat session:', error);
                    alert('An error occurred while creating the new chat session. Please check your network connection and try again.');
                    newChatButton.disabled = false;
                    newChatButton.innerHTML = `
                        <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6"></path></svg>
                        New Chat`;
                }
            });
        } else {
            console.warn("app-ui.js: 'New Chat' form or its CSRF input field was not found after sidebar load. AJAX submission not configured.");
        }
        // --- END OF MODIFIED PART ---
        
        return true;
    } catch (error) {
        console.error('app-ui.js: Could not load sidebar due to an error:', error);
        sidebarTarget.innerHTML = '<p class="p-4 text-red-400">Error loading sidebar content (exception).</p>';
        return false;
    }
}

export async function handleDeleteSession(sessionId, sessionName, apiEndpointForList, listElementId, chatPageBaseUrl) {
    if (!window.confirm(`Are you sure you want to delete the session "${sessionName}"? This action cannot be undone.`)) {
        return;
    }

    console.log(`app-ui.js: Attempting to delete session: ${sessionId}`);
    
    // *** ADDED DETAILED LOGGING FOR window.csrfTokenRaw ***
    const currentTokenValue = window.csrfTokenRaw;
    const isPlaceholder = currentTokenValue === "%%CSRF_TOKEN_RAW%%";
    const isEmptyOrWhitespace = !currentTokenValue || currentTokenValue.trim() === "";

    console.log(`app-ui.js (handleDeleteSession): Checking CSRF token. 
        Value: '${currentTokenValue}'
        Is Placeholder: ${isPlaceholder}
        Is Undefined/Null/Empty/Whitespace: ${isEmptyOrWhitespace}`);

    if (!currentTokenValue || isPlaceholder || isEmptyOrWhitespace) {
        console.error(`app-ui.js: CSRF token is missing or invalid. Cannot proceed with delete operation. 
            Raw Value: '${currentTokenValue}', 
            Is Placeholder: ${isPlaceholder}, 
            Is Empty: ${isEmptyOrWhitespace}`);
        alert("Error: Could not perform action: CSRF token not found. Please refresh the page.");
        return;
    }

    try {
        const response = await fetch(`/api/sessions/${sessionId}`, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': currentTokenValue // Use the validated token
            }
        });

        if (response.status === 204) {
            console.log(`app-ui.js: Session ${sessionId} successfully deleted from server.`);
            const currentPath = window.location.pathname;
            const base = chatPageBaseUrl.endsWith('/') ? chatPageBaseUrl : chatPageBaseUrl + '/';
            const deletedSessionPath = `${base}${sessionId}`;

            if (currentPath === deletedSessionPath || currentPath === `${deletedSessionPath}/`) {
                window.location.href = '/'; 
            } else {
                await populateSessionList(apiEndpointForList, listElementId, chatPageBaseUrl);
            }
        } else {
            const errorData = await response.json().catch(() => ({ detail: `Failed to delete session. Server responded with status: ${response.status}` }));
            console.error(`app-ui.js: Failed to delete session ${sessionId}. Status: ${response.status}`, errorData);
            alert(`Error deleting session: ${errorData.detail || response.statusText || `Status ${response.status}`}`);
        }
    } catch (error) {
        console.error('app-ui.js: Error during delete session request:', error);
        alert('An error occurred while trying to delete the session. Please check the console for details.');
    }
}

/**
 * Fetches and populates the list of user sessions in the sidebar.
 * @param {string} apiEndpoint - API endpoint to fetch sessions (e.g., '/api/sessions').
 * @param {string} listElementId - ID of the ul element to populate.
 * @param {string} chatPageBaseUrl - Base URL for constructing chat page links (e.g., '/chat/').
 */
export async function populateSessionList(apiEndpoint = '/api/sessions', listElementId = 'session-list', chatPageBaseUrl = '/chat/') {
    const sessionListElement = document.getElementById(listElementId);
    if (!sessionListElement) {
        console.error(`app-ui.js: Session list element with ID '${listElementId}' not found.`);
        return;
    }
    sessionListElement.innerHTML = '<li class="px-3 py-1 text-gray-400 italic text-sm">Loading sessions...</li>';

    try {
        console.log(`app-ui.js: Fetching sessions from: ${apiEndpoint}`);
        const response = await fetch(apiEndpoint); // GET request, no CSRF header needed for this

        if (!response.ok) {
            const errorText = await response.text().catch(() => "Unknown server error");
            console.error(`app-ui.js: Failed to fetch sessions from ${apiEndpoint}. Status: ${response.status}. Response: ${errorText}`);
            sessionListElement.innerHTML = `<li class="px-3 py-1 text-red-400 text-sm">Error loading sessions (status: ${response.status}).</li>`;
            return; // Stop further processing
        }
        
        const sessions = await response.json();
        if (!Array.isArray(sessions)) {
            console.error(`app-ui.js: Data from ${apiEndpoint} is not an array:`, sessions);
            sessionListElement.innerHTML = '<li class="px-3 py-1 text-red-400 text-sm">Error: Invalid session data format.</li>';
            return; // Stop further processing
        }

        if (sessions.length === 0) {
            sessionListElement.innerHTML = '<li class="px-3 py-1 text-gray-400 text-sm italic">No active sessions found.</li>';
        } else {
            sessionListElement.innerHTML = ''; // Clear "Loading..." or previous list
            sessions.forEach(session => {
                if (!session.id || typeof session.name === 'undefined') { // Check for essential properties
                    console.warn('app-ui.js: Session object is missing id or name:', session);
                    return; // Skip this malformed session entry
                }

                const listItem = document.createElement('li');
                // Added group for hover effects on children (like the delete button)
                listItem.className = 'flex items-center justify-between pr-2 group hover:bg-gray-700 rounded-md transition-colors duration-100';

                const link = document.createElement('a');
                const base = chatPageBaseUrl.endsWith('/') ? chatPageBaseUrl : chatPageBaseUrl + '/';
                link.href = `${base}${session.id}`;
                
                // Apply active state styling if this session's link matches the current page path
                const currentPath = window.location.pathname;
                const isActive = currentPath === link.pathname || currentPath === `${link.pathname}/`;
                
                link.className = `block pl-3 pr-1 py-2 text-gray-300 group-hover:text-white rounded-l-md text-sm truncate flex-grow ${isActive ? 'bg-gray-700 text-white font-semibold' : 'hover:text-white'}`;
                if (isActive) {
                    listItem.classList.add('bg-gray-700'); // Highlight the whole li item if active
                }
                
                let lastActiveDisplay = "Never";
                if (session.last_active) {
                    try {
                        // Format date for better readability; toLocaleString can be verbose
                        const date = new Date(session.last_active);
                        lastActiveDisplay = `${date.toLocaleDateString()} ${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
                    } catch (e) {
                        console.warn("app-ui.js: Could not parse last_active date:", session.last_active, e);
                        lastActiveDisplay = session.last_active; // show raw if parsing fails
                    }
                }
                link.title = `${session.name || 'Unnamed Session'}\nLast active: ${lastActiveDisplay}`;
                link.textContent = session.name || `Session ${session.id.substring(0, 8)}...`;
                
                const deleteButton = document.createElement('button');
                deleteButton.innerHTML = '&#x2715;'; // Multiplication X, a common delete symbol
                deleteButton.className = 'ml-2 p-1 text-gray-500 hover:text-red-400 focus:outline-none rounded-full hover:bg-gray-600 transition-colors duration-150 ease-in-out text-xs opacity-0 group-hover:opacity-100 focus:opacity-100 flex-shrink-0'; 
                deleteButton.title = `Delete session: ${session.name || 'Unnamed Session'}`;
                deleteButton.setAttribute('aria-label', `Delete session: ${session.name || 'Unnamed Session'}`);
                
                deleteButton.onclick = (event) => {
                    event.preventDefault(); // Prevent link navigation if button is somehow inside <a> or form
                    event.stopPropagation(); // Prevent triggering link navigation or other parent events
                    handleDeleteSession(session.id, session.name || 'Unnamed Session', apiEndpoint, listElementId, base);
                };

                listItem.appendChild(link);
                listItem.appendChild(deleteButton);
                sessionListElement.appendChild(listItem);
            });
        }
        console.log(`app-ui.js: Session list successfully populated from ${apiEndpoint} with ${sessions.length} sessions.`);

    } catch (error) {
        console.error('app-ui.js: Error populating session list:', error);
        if (sessionListElement) { // Check again in case it became null
            sessionListElement.innerHTML = `<li class="px-3 py-1 text-red-400 text-sm">Could not load sessions. Error: ${error.message || 'Unknown error'}</li>`;
        }
    }
}