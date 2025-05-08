// --- JS Imports ---
import { marked } from 'marked';
import katex from 'katex';
import Prism from 'prismjs';

// --- Prism Components ---

import 'prismjs/components/prism-clike';
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-javascript';
import 'prismjs/components/prism-css';
import 'prismjs/components/prism-bash';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-yaml';
import 'prismjs/components/prism-sql';
import 'prismjs/components/prism-java';
import 'prismjs/components/prism-csharp';
import 'prismjs/components/prism-go';
import 'prismjs/components/prism-rust';
import 'prismjs/components/prism-docker';
import 'prismjs/components/prism-typescript';
import 'prismjs/components/prism-c';
import 'prismjs/components/prism-cpp';
// import 'prismjs/components/prism-php'; // not working
// import 'prismjs/components/prism-ruby'; // not working
// --------------------------

// --- Constants and Variables ---
const chatHistory = document.getElementById('chat-history');
const chatForm = document.getElementById('chat-form');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const thinkCheckbox = document.getElementById('think-checkbox');

let websocket;
const clientId = `web-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
let currentTurnId = 0;

const MODE_ANSWER = 'MODE_ANSWER';
const MODE_SEEKING_CODE_LANGUAGE = 'MODE_SEEKING_CODE_LANGUAGE';
const MODE_INSIDE_CODE_BLOCK = 'MODE_INSIDE_CODE_BLOCK';
const MODE_KATEX_BUFFERING_INLINE = 'MODE_KATEX_BUFFERING_INLINE';
const MODE_KATEX_BUFFERING_DISPLAY = 'MODE_KATEX_BUFFERING_DISPLAY';
const MODE_MAYBE_START_DISPLAY_KATEX = 'MODE_MAYBE_START_DISPLAY_KATEX';
const MODE_SEEKING_TAG = 'MODE_SEEKING_TAG';
const MODE_INSIDE_THINK = 'MODE_INSIDE_THINK';
const MODE_MAYBE_END_THINK = 'MODE_MAYBE_END_THINK';
const NO_THINK_PREFIX = "\\no_think";
const MODE_SEEKING_CODE_START_FENCE = 'MODE_SEEKING_CODE_START_FENCE';
const MODE_SEEKING_CODE_END_FENCE = 'MODE_SEEKING_CODE_END_FENCE';

let currentProcessingMode = MODE_ANSWER;
let langBuffer = '';
let currentCodeBlockLang = ''; // Stores the language name (e.g., 'python')
let currentCodeBlockElement = null; // The <code> element
let currentCodeBlockPreElement = null; // The <pre> element wrapping the code
let katexBuffer = '';
let currentKatexMarkerId = null;
let thinkBuffer = '';
let tagBuffer = '';
let fenceBuffer = ''; // Added for fence detection
let currentAiTurnContainer = null;
let currentAnswerElement = null;
let currentThinkingArea = null;
let currentThinkingPreElement = null;
let currentCodeBlocksArea = null;
let codeBlockCounterThisTurn = 0;
let thinkingRequestedForCurrentTurn = false;
let accumulatedAnswerText = '';
let lastAppendedNode = null;
let hasThinkingContentArrivedThisTurn = false;
let firstAnswerTokenReceived = false;
var currentUserInfo = null

const FENCE = '```';
const THINK_START_TAG = '<think>';
const THINK_END_TAG = '</think>';
const KATEX_PLACEHOLDER_PREFIX = '%%KATEX_PLACEHOLDER_';
const KATEX_RENDERED_ATTR = 'data-katex-rendered';


/**
 * Extracts the session ID from the current URL path.
 * Assumes URL is like /chat/SESSION_ID/...
 * Returns the session ID string or null if not found or invalid.
 */
function getSessionIdFromPath() {
    const pathName = window.location.pathname; // Get the path part of the URL (e.g., "/chat/some-session-id")
    const pathParts = pathName.split('/');    // Split the path by "/"
                                            // For "/chat/some-session-id", pathParts will be ["", "chat", "some-session-id"]

    // Check if the path structure is as expected:
    // 1. pathParts.length >= 3 (e.g., "", "chat", "session_id")
    // 2. pathParts[1] is exactly "chat"
    if (pathParts.length >= 3 && pathParts[1] === 'chat') {
        const sessionId = pathParts[2]; // The session ID is the third part
        if (sessionId && sessionId.trim() !== "") { // Ensure it's not empty
            // console.log("Extracted session ID:", sessionId); // Keep for debugging if needed
            return sessionId;
        } else {
            console.error("Session ID extracted from path is empty or invalid.");
            return null;
        }
    }

    // console.log("Not on a chat page or session ID invalid:", pathName); // Keep for debugging if needed
    return null; // Return null if the path doesn't match the expected structure
}

// --- Utility Functions ---

function scrollToBottom(behavior = 'auto') {
    const isNearBottom = chatHistory.scrollHeight - chatHistory.scrollTop - chatHistory.clientHeight < 100;
    if (isNearBottom) {
        requestAnimationFrame(() => {
            chatHistory.scrollTo({ top: chatHistory.scrollHeight, behavior: behavior });
        });
    }
}

// --- Utility Functions --- 
// (debounce function is here)

function throttle(func, limit) {
    let lastFunc;
    let lastRan;
    return function throttledFunction(...args) {
      const context = this; // Capture the context
      if (!lastRan) {
        // If it hasn't run yet, run it immediately
        func.apply(context, args);
        lastRan = Date.now();
      } else {
        clearTimeout(lastFunc); // Clear any previously scheduled run
        // Schedule the next run after the limit has passed
        lastFunc = setTimeout(function() {
          // Check if enough time has passed since the last execution
          if ((Date.now() - lastRan) >= limit) {
            func.apply(context, args);
            lastRan = Date.now(); // Update the time it last ran
          }
        }, limit - (Date.now() - lastRan)); // Calculate remaining time needed
      }
    };
  }
  
  // (debouncedStreamHighlight definition will be replaced next)
  // (Rest of your utility functions)

function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
};

function getCursorPosition(parentElement) {
    const selection = window.getSelection();
    if (selection.rangeCount === 0) return -1;

    const range = selection.getRangeAt(0);
    if (!parentElement.contains(range.startContainer)) {
        return -1;
    }

    const preSelectionRange = range.cloneRange();
    preSelectionRange.selectNodeContents(parentElement);
    try {
        preSelectionRange.setEnd(range.startContainer, range.startOffset);
    } catch (e) {
        console.error("Error setting preSelectionRange end:", e, "Range:", range);
        return -1;
    }
    return preSelectionRange.toString().length;
}

function setCursorPosition(parentElement, offset) {
    const selection = window.getSelection();
    if (!selection) return;

    const range = document.createRange();
    let charCount = 0;
    let foundStart = false;

    function findNodeAndOffset(node) {
        if (foundStart) return;

        if (node.nodeType === Node.TEXT_NODE) {
            const nextCharCount = charCount + node.length;
            if (!foundStart && offset >= charCount && offset <= nextCharCount) {
                try {
                    const offsetInNode = offset - charCount;
                    range.setStart(node, offsetInNode);
                    foundStart = true;
                } catch (e) {
                    console.error("Error setting range start:", e, "Node:", node, "Offset:", offsetInNode);
                }
            }
            charCount = nextCharCount;
        } else {
            for (let i = 0; i < node.childNodes.length; i++) {
                findNodeAndOffset(node.childNodes[i]);
                if (foundStart) break;
            }
        }
    }

    findNodeAndOffset(parentElement);

    if (foundStart) {
        range.collapse(true);
        selection.removeAllRanges();
        selection.addRange(range);
    } else {
        range.selectNodeContents(parentElement);
        range.collapse(false);
        selection.removeAllRanges();
        selection.addRange(range);
        console.warn(`Could not set cursor precisely at offset ${offset}. Placed at end.`);
    }
    parentElement.focus();
}

/**
 * Fetches the list of user sessions from the API and displays them in the sidebar.
 */
async function fetchAndDisplaySessions() {
    console.log("[fetchAndDisplaySessions] Function called."); // Log: Start of function

    const sessionListElement = document.getElementById('session-list');
    const chatSessionTitle = document.getElementById('chat-session-title');

    if (!sessionListElement) {
        console.log("[fetchAndDisplaySessions] Session list element (#session-list) not found. Exiting.");
        return;
    }

    console.log("[fetchAndDisplaySessions] Setting loading state.");
    sessionListElement.innerHTML = '<li class="px-3 py-1 text-gray-400 italic text-sm">Loading sessions...</li>';

    try {
        console.log("[fetchAndDisplaySessions] Making fetch request to /api/sessions...");
        const response = await fetch('/api/sessions');
        console.log(`[fetchAndDisplaySessions] Fetch response status: ${response.status}`); // Log: Response status

        if (!response.ok) {
            let errorDetail = `HTTP error ${response.status}`;
            try {
                const errorJson = await response.json();
                errorDetail = errorJson.detail || errorDetail;
            } catch (e) { /* Ignore */ }
            console.error(`[fetchAndDisplaySessions] Error fetching sessions: ${errorDetail}`);
            sessionListElement.innerHTML = `<li class="px-3 py-1 text-red-400 italic text-sm">Error loading sessions</li>`;
            return;
        }

        const sessions = await response.json();
        console.log("[fetchAndDisplaySessions] Received sessions:", sessions); // Log: The received data

        sessionListElement.innerHTML = ''; // Clear loading/error

        if (sessions.length === 0) {
            console.log("[fetchAndDisplaySessions] No sessions found. Displaying message.");
            sessionListElement.innerHTML = '<li class="px-3 py-1 text-gray-400 italic text-sm">No sessions yet</li>';
        } else {
            console.log(`[fetchAndDisplaySessions] Processing ${sessions.length} sessions...`);
            const activeSessionId = getSessionIdFromPath(); // Assumes getSessionIdFromPath() exists and works
            console.log(`[fetchAndDisplaySessions] Current active session ID from path: ${activeSessionId}`);

            sessions.forEach((session, index) => {
                // console.log(`[fetchAndDisplaySessions] Adding session ${index + 1}: ID=${session.id}, Name=${session.name}`); // Optional: Log each session
                const li = document.createElement('li');
                const a = document.createElement('a');
                a.href = `/chat/${session.id}`;
                a.classList.add('block', 'px-3', 'py-2', 'rounded-md', 'text-sm', 'text-gray-300', 'hover:bg-gray-700', 'hover:text-white', 'truncate');

                if (session.id === activeSessionId) {
                    // console.log(`[fetchAndDisplaySessions] Highlighting active session: ${session.id}`);
                    a.classList.add('bg-gray-900', 'text-white');
                    a.setAttribute('aria-current', 'page');
                    if (chatSessionTitle) {
                        chatSessionTitle.textContent = session.name || `Chat Session ${session.id.substring(0, 4)}`;
                    }
                }

                a.textContent = session.name || `Session ${session.id.substring(0, 8)}`;
                a.title = session.name || `Session ${session.id}`;

                // Placeholder for Rename/Delete buttons
                // const iconsSpan = document.createElement('span');
                // iconsSpan.innerHTML = `...`;
                // a.appendChild(iconsSpan);

                li.appendChild(a);
                sessionListElement.appendChild(li);
            });
            console.log("[fetchAndDisplaySessions] Finished processing sessions.");
        }

    } catch (error) {
        console.error("[fetchAndDisplaySessions] Failed to fetch or display sessions:", error);
        sessionListElement.innerHTML = `<li class="px-3 py-1 text-red-400 italic text-sm">Error loading sessions</li>`;
    }
}

/**
 * Adds a user's message to the chat history UI.
 * Displays the current user's dynamic name and strips the NO_THINK_PREFIX 
 * from the displayed text if the user typed it.
 * Uses explicit window.currentUserInfo check.
 * @param {string} text - The raw text of the user's message as typed.
 */
function addUserMessage(text) {
    // Log the global variable directly at the start of the function call
    console.log(">>> addUserMessage called. window.currentUserInfo is:", window.currentUserInfo); 

    const messageElement = document.createElement('div');
    messageElement.classList.add('message', 'user-message', 'p-3', 'rounded-lg', 'max-w-xl', 'mb-2', 'break-words', 'flex', 'flex-col', 'bg-emerald-100', 'self-end', 'ml-auto');
    messageElement.setAttribute('data-sender', 'user');

    const senderElem = document.createElement('p');
    senderElem.classList.add('font-semibold', 'text-sm', 'mb-1', 'text-emerald-700');

    let userName = 'User'; // Default name
    console.log(">>> addUserMessage: Initial userName:", userName); 

    // --- MODIFIED: Explicitly check window.currentUserInfo ---
    if (typeof window.currentUserInfo === 'object' && window.currentUserInfo !== null && window.currentUserInfo.name) {
        console.log(">>> addUserMessage: Condition met. window.currentUserInfo.name is:", window.currentUserInfo.name); 
        userName = window.currentUserInfo.name; // Assign from the global object
        console.log(">>> addUserMessage: userName reassigned to:", userName); 
    } else {
        // Log details if the condition fails
        console.warn(`[addUserMessage] Condition failed. typeof window.currentUserInfo: ${typeof window.currentUserInfo}, window.currentUserInfo value: ${JSON.stringify(window.currentUserInfo)}, has name property: ${window.currentUserInfo ? window.currentUserInfo.hasOwnProperty('name') : 'N/A'}. Displaying default 'User'.`);
    }
    // --- END OF MODIFICATION ---

    console.log(`>>> Before setting textContent, userName is: '${userName}'`); 
    
    // escapeHTML should be defined globally
    senderElem.textContent = escapeHTML(userName); 
    
    console.log(`>>> After setting textContent, senderElem.textContent is: '${senderElem.textContent}'`); 

    messageElement.appendChild(senderElem);

    // --- Message Content ---
    const contentElem = document.createElement('div');
    contentElem.classList.add('text-gray-800', 'text-sm', 'message-content');
    let displayedText = text; 
    if (typeof NO_THINK_PREFIX === 'string' && NO_THINK_PREFIX.length > 0 && displayedText.startsWith(NO_THINK_PREFIX)) {
        displayedText = displayedText.substring(NO_THINK_PREFIX.length);
    }
    contentElem.textContent = displayedText;
    messageElement.appendChild(contentElem);

    // --- Timestamp ---
    const timestampElem = document.createElement('p');
    timestampElem.classList.add('text-xs', 'text-slate-500', 'mt-1', 'text-right');
    timestampElem.textContent = new Date().toLocaleString(); 
    messageElement.appendChild(timestampElem);
    
    if (chatHistory) { 
        chatHistory.appendChild(messageElement);
        setTimeout(() => scrollToBottom('smooth'), 50);
    } else {
        console.error("[addUserMessage] chatHistory element not found.");
    }
}





function addSystemMessage(text) {
   const messageElement = document.createElement('div');
   messageElement.classList.add('system-message');
   messageElement.textContent = text;
   chatHistory.appendChild(messageElement);
   setTimeout(() => scrollToBottom('smooth'), 50);
}

function addErrorMessage(text) {
   console.error("[UI ERROR] ", text);
   const messageElement = document.createElement('div');
   messageElement.classList.add('error-message');
   messageElement.textContent = `Error: ${text}`;
   if(currentAiTurnContainer) {
       const target = currentAnswerElement || currentCodeBlocksArea || currentAiTurnContainer;
       target.appendChild(messageElement);
   } else { chatHistory.appendChild(messageElement); }
   setTimeout(() => scrollToBottom('smooth'), 50);
}

function setInputDisabledState(disabled) {
    messageInput.disabled = disabled;
    sendButton.disabled = disabled;
    if (thinkCheckbox) {
        thinkCheckbox.disabled = disabled;
    }
}

/**
 * Sets up the DOM structure for a new turn from the AI, including thinking area,
 * answer bubble (with sender name), and code blocks area.
 */
function setupNewAiTurn() {
    currentTurnId++;
    codeBlockCounterThisTurn = 0;
    accumulatedAnswerText = '';
    lastAppendedNode = null;
    thinkBuffer = '';
    hasThinkingContentArrivedThisTurn = false;
    firstAnswerTokenReceived = false;

    currentAiTurnContainer = null;
    currentThinkingArea = null;
    currentThinkingPreElement = null;
    currentAnswerElement = null;
    currentCodeBlocksArea = null;

    console.log(`[setupNewAiTurn] Starting setup for Turn ID: ${currentTurnId}. thinkingRequestedForCurrentTurn is: ${thinkingRequestedForCurrentTurn}`);

    currentAiTurnContainer = document.createElement('div');
    currentAiTurnContainer.classList.add('ai-turn-container');
    currentAiTurnContainer.dataset.turnId = currentTurnId;

    // --- Thinking Area Setup (remains the same) ---
    currentThinkingArea = document.createElement('div');
    currentThinkingArea.classList.add('thinking-area');
    currentThinkingArea.dataset.turnId = currentTurnId;
    if (!thinkingRequestedForCurrentTurn) {
        currentThinkingArea.style.display = 'none';
    }
    const details = document.createElement('details');
    details.id = `thinking-details-${currentTurnId}`;
    const summary = document.createElement('summary');
    summary.classList.add('thinking-summary');
    const summaryTextSpan = document.createElement('span');
    summaryTextSpan.classList.add('text');
    summaryTextSpan.textContent = 'Show Thinking';
    const summaryDotsSpan = document.createElement('span');
    summaryDotsSpan.classList.add('dots');
    summary.appendChild(summaryTextSpan);
    summary.appendChild(summaryDotsSpan);
    currentThinkingPreElement = document.createElement('pre');
    details.appendChild(summary);
    details.appendChild(currentThinkingPreElement);
    currentThinkingArea.appendChild(details);
    currentAiTurnContainer.appendChild(currentThinkingArea);
    details.addEventListener('toggle', (event) => {
        const textSpan = event.target.querySelector('.thinking-summary .text');
        if (!textSpan) return;
        textSpan.textContent = event.target.open ? 'Hide Thinking' : 'Show Thinking';
    });
    // --- End Thinking Area Setup ---

    // --- Answer Element (AI Message Bubble) Setup ---
    currentAnswerElement = document.createElement('div');
    currentAnswerElement.classList.add('message', 'ai-message');
    // Add styling classes similar to historical AI messages for consistency
    currentAnswerElement.classList.add('p-3', 'rounded-lg', 'max-w-xl', 'mb-2', 'break-words', 'flex', 'flex-col', 'bg-sky-100', 'self-start', 'mr-auto');


    // **NEW**: Add Sender Name ("AI") to the live answer bubble
    const senderElem = document.createElement('p');
    senderElem.classList.add('font-semibold', 'text-sm', 'mb-1', 'text-sky-700');
    senderElem.textContent = 'AI'; // Or a dynamic AI name if available
    currentAnswerElement.appendChild(senderElem); // Prepend or append as preferred; prepending here

    // Div to hold the actual streaming content (will be populated by appendToAnswer/formatAnswerBubbleFinal)
    // This is effectively the 'message-content' part of the bubble.
    // We create a dedicated div for it so the sender name and timestamp can be siblings.
    const liveContentDiv = document.createElement('div');
    liveContentDiv.classList.add('text-gray-800', 'text-sm', 'message-content', 'live-ai-content-area'); // Added a specific class
    currentAnswerElement.appendChild(liveContentDiv);


    if (thinkingRequestedForCurrentTurn) {
        currentAnswerElement.style.display = 'none'; // Hide bubble if thinking is shown first
    } else {
        // Show answer bubble immediately with loading dots if no thinking display
        const loadingSpan = document.createElement('span');
        loadingSpan.classList.add('loading-dots');
        liveContentDiv.appendChild(loadingSpan); // Add loading dots to the content area
    }
    currentAiTurnContainer.appendChild(currentAnswerElement);

    // --- Code Blocks Area Setup (remains the same) ---
    currentCodeBlocksArea = document.createElement('div');
    currentCodeBlocksArea.classList.add('code-blocks-area');
    currentAiTurnContainer.appendChild(currentCodeBlocksArea);
    // --- End Code Blocks Area Setup ---

    chatHistory.appendChild(currentAiTurnContainer);
    console.log(`[setupNewAiTurn] Finished setup for Turn ID: ${currentTurnId}.`);
}

// You'll also need to adjust `appendToAnswer` and `formatAnswerBubbleFinal`
// to target the `liveContentDiv` within `currentAnswerElement` for the actual message content,
// instead of `currentAnswerElement` directly, to keep the sender name and future timestamp separate.

// Example adjustment for appendToAnswer (conceptual):
// Original: currentAnswerElement.appendChild(node);
// New: const targetContentArea = currentAnswerElement.querySelector('.live-ai-content-area');
//      if (targetContentArea) targetContentArea.appendChild(node); else currentAnswerElement.appendChild(node);
// Similar logic for text nodes and for formatAnswerBubbleFinal's innerHTML operations.


function appendRawTextToThinkingArea(text) {
    if (text && text.trim().length > 0) {
        // console.log(`[appendRawTextToThinkingArea] Received non-empty raw think text: "${text}" (length: ${text?.length})`);
    } else if (text !== null) {
        // console.log(`[appendRawTextToThinkingArea] Received empty or whitespace raw think text (length: ${text?.length})`);
    }

    // console.log(`[appendRawTextToThinkingArea] Checking condition: !thinkingRequestedForCurrentTurn is ${!thinkingRequestedForCurrentTurn} (value: ${thinkingRequestedForCurrentTurn})`);
    if (!thinkingRequestedForCurrentTurn) {
        // console.log("[appendRawTextToThinkingArea] Condition met (!thinkingRequested). Returning early. Should NOT display thinking.");
        return;
    }

    // console.log("[appendRawTextToThinkingArea] Proceeding because thinking was requested.");

    if (!currentThinkingArea || !currentThinkingPreElement || text.length === 0) {
        return;
    }

    if (!hasThinkingContentArrivedThisTurn) {
        if (currentThinkingArea.style.display === 'none') {
            // console.log("[appendRawTextToThinkingArea] First think chunk (and requested). Making area visible NOW. Display style was: " + currentThinkingArea.style.display);
            currentThinkingArea.style.display = '';
        }
        hasThinkingContentArrivedThisTurn = true;
    }

    try {
        currentThinkingPreElement.appendChild(document.createTextNode(text));
    } catch (appendError) {
         console.error("[appendRawTextToThinkingArea] Error appending text node:", appendError, "Pre Element:", currentThinkingPreElement, "Text:", text);
        return;
    }

    const detailsElement = currentThinkingPreElement.closest('details');
    if (detailsElement && detailsElement.open) {
         const isNearBottom = currentThinkingPreElement.scrollHeight - currentThinkingPreElement.scrollTop - currentThinkingPreElement.clientHeight < 50;
         if(isNearBottom) {
             requestAnimationFrame(() => { currentThinkingPreElement.scrollTop = currentThinkingPreElement.scrollHeight; });
         }
    }
}

function appendCodeReference() {
    if (!currentAnswerElement) {
        console.error("Attempted to append code reference to null answer bubble!");
        return;
    }
    if (codeBlockCounterThisTurn > 0) {
         const refSpan = document.createElement('span');
         refSpan.classList.add('code-reference');
         refSpan.textContent = `[Code ${codeBlockCounterThisTurn}]`;
         currentAnswerElement.appendChild(refSpan);
         lastAppendedNode = refSpan;
    } else {
        console.warn("appendCodeReference called but codeBlockCounterThisTurn is 0.");
    }
}

/**
 * Finalizes the AI answer bubble content with Markdown and KaTeX processing.
 * Targets the specific content div within the answer bubble.
 */
function formatAnswerBubbleFinal() {
    if (!currentAnswerElement) {
        console.warn("[formatAnswerBubbleFinal] Skipping: currentAnswerElement is null.");
        accumulatedAnswerText = ''; lastAppendedNode = null; firstAnswerTokenReceived = false; return;
    }

    // **MODIFIED**: Target the dedicated content area
    const targetContentArea = currentAnswerElement.querySelector('.live-ai-content-area');
    if (!targetContentArea) {
        console.error("[formatAnswerBubbleFinal] '.live-ai-content-area' not found. Cannot format content.");
        // Fallback: if somehow the structure is missing, try to use currentAnswerElement directly
        // but this will mix with sender name. This is mostly a safeguard.
        if (accumulatedAnswerText.trim().length > 0) {
             currentAnswerElement.innerHTML += marked.parse(accumulatedAnswerText, { mangle: false, headerIds: false, gfm: true, breaks: true, sanitize: false });
        }
        accumulatedAnswerText = ''; lastAppendedNode = null; firstAnswerTokenReceived = false; return;
    }
    
    // Original logic from formatAnswerBubbleFinal, but operating on `targetContentArea`

    // Ensure the main answer bubble is visible if it has content or accumulated text
    if (currentAnswerElement.style.display === 'none' && (targetContentArea.hasChildNodes() || accumulatedAnswerText.trim().length > 0)) {
        currentAnswerElement.style.display = ''; // Show the whole bubble
        const loadingDots = targetContentArea.querySelector('.loading-dots'); // Dots are inside targetContentArea
        if (loadingDots) loadingDots.remove();
    }

    const hasVisualContent = targetContentArea.hasChildNodes() && !targetContentArea.querySelector('.loading-dots');
    const hasAccumulatedContent = accumulatedAnswerText.trim().length > 0;

    if (!hasVisualContent && !hasAccumulatedContent) {
        const loadingDots = targetContentArea.querySelector('.loading-dots');
        if (loadingDots) loadingDots.remove();
        accumulatedAnswerText = '';
        lastAppendedNode = null;
        // firstAnswerTokenReceived should remain true if it was set, or handle as needed
        return;
    }

    try {
        const storedKatexNodes = {};
        let placeholderIndex = 0;
        let textForMarkdown = accumulatedAnswerText; // Start with accumulated text

        // Find KaTeX spans already rendered by the live streaming logic within targetContentArea
        const katexSpans = Array.from(targetContentArea.children).filter(el => el.matches(`span[${KATEX_RENDERED_ATTR}="true"]`));

        if (katexSpans.length > 0) {
            katexSpans.forEach((el) => {
                if (!el.parentNode) return; // Should be targetContentArea
                const placeholder = `${KATEX_PLACEHOLDER_PREFIX}${placeholderIndex++}`; // KATEX_PLACEHOLDER_PREFIX is global
                storedKatexNodes[placeholder] = el.cloneNode(true);
                try {
                    // Replace the KaTeX span with a text node placeholder
                    el.parentNode.replaceChild(document.createTextNode(placeholder), el);
                } catch (replaceError) {
                    console.error(`[formatAnswerBubbleFinal] Error replacing KaTeX node with placeholder ${placeholder}:`, replaceError, "Node:", el);
                    try { el.parentNode.removeChild(el); } catch (removeError) { console.error("Failed to remove problematic KaTeX node:", removeError); }
                }
            });
            // After replacement, the innerHTML of targetContentArea contains text and placeholders
            textForMarkdown = targetContentArea.innerHTML;
        } else {
             // If no KaTeX spans but there was visual content (e.g. just text nodes from appendToAnswer)
             // and also accumulated text, we should clear the visual DOM and use accumulated text.
            if (hasVisualContent && hasAccumulatedContent) {
                targetContentArea.innerHTML = ''; // Clear existing simple text nodes
            } else if (!hasAccumulatedContent && hasVisualContent) {
                // Only visual content (e.g. from appendToAnswer(null, node) where node was not KaTeX)
                // and no accumulated text. Use existing innerHTML.
                textForMarkdown = targetContentArea.innerHTML;
            }
            // If only accumulated text, textForMarkdown is already set.
        }
        
        if (textForMarkdown.trim().length === 0 && Object.keys(storedKatexNodes).length === 0) {
             // No text to parse and no KaTeX to reinsert.
        } else {
            // Parse the potentially modified innerHTML or accumulated text
            const markdownHtml = marked.parse(textForMarkdown, {
                mangle: false, headerIds: false, gfm: true, breaks: true, sanitize: false
            });
            targetContentArea.innerHTML = markdownHtml; // Set the parsed HTML into the content area
        }


        // Reinsert KaTeX nodes if any were stored
        if (Object.keys(storedKatexNodes).length > 0) {
            const walker = document.createTreeWalker(targetContentArea, NodeFilter.SHOW_TEXT);
            let node;
            const textNodesContainingPlaceholders = [];
            while (node = walker.nextNode()) {
                if (node.nodeValue && node.nodeValue.includes(KATEX_PLACEHOLDER_PREFIX)) {
                    textNodesContainingPlaceholders.push(node);
                }
            }

            textNodesContainingPlaceholders.forEach(textNode => {
                let currentNodeValue = textNode.nodeValue;
                let parent = textNode.parentNode;
                if (!parent) return; // Should be within targetContentArea

                const fragment = document.createDocumentFragment();
                let lastSplitEnd = 0;
                const placeholderScanRegex = new RegExp(`(${KATEX_PLACEHOLDER_PREFIX}\\d+)`, 'g');
                let placeholderMatch;

                while((placeholderMatch = placeholderScanRegex.exec(currentNodeValue)) !== null) {
                    const placeholder = placeholderMatch[1];
                    const matchIndex = placeholderMatch.index;
                    if (matchIndex > lastSplitEnd) {
                        fragment.appendChild(document.createTextNode(currentNodeValue.substring(lastSplitEnd, matchIndex)));
                    }
                    if (storedKatexNodes[placeholder]) {
                        fragment.appendChild(storedKatexNodes[placeholder].cloneNode(true));
                    } else {
                        fragment.appendChild(document.createTextNode(placeholder)); // Fallback
                    }
                    lastSplitEnd = placeholderScanRegex.lastIndex;
                }
                if (lastSplitEnd < currentNodeValue.length) {
                    fragment.appendChild(document.createTextNode(currentNodeValue.substring(lastSplitEnd)));
                }
                parent.replaceChild(fragment, textNode);
            });
        }
    } catch (error) {
        console.error("Error during final Markdown/KaTeX formatting in live AI bubble:", error);
        addErrorMessage("Failed to perform final message formatting for AI response.");
        if (targetContentArea && accumulatedAnswerText.trim().length > 0) {
            targetContentArea.textContent = accumulatedAnswerText; // Fallback to raw accumulated text
        }
    }
    accumulatedAnswerText = ''; // Clear after processing
    lastAppendedNode = null; // Reset for the next stream
    // firstAnswerTokenReceived remains true.
}


function resetStreamingState() {
    // console.log("[DEBUG] Resetting streaming state.");
    currentProcessingMode = MODE_ANSWER;
    langBuffer = ''; currentCodeBlockLang = '';
    currentCodeBlockElement = null; currentCodeBlockPreElement = null;
    katexBuffer = ''; currentKatexMarkerId = null;
    thinkBuffer = ''; tagBuffer = ''; fenceBuffer = ''; // Reset fenceBuffer
    lastAppendedNode = null;
    thinkingRequestedForCurrentTurn = false;
}

function renderAndReplaceKatex(isDisplay, markerId) {

    if (!currentAnswerElement || !markerId || katexBuffer === null || katexBuffer === undefined) {
        console.error("[KaTeX Replace Marker] Error: Invalid state (element, markerId, or buffer). MarkerID:", markerId, "Buffer:", katexBuffer);
        if (markerId && currentAnswerElement) {
             try {
                 const strayMarker = currentAnswerElement.querySelector(`span[data-katex-start-id="${markerId}"]`);
                 if (strayMarker?.parentNode) strayMarker.parentNode.removeChild(strayMarker);
             } catch (e) { console.error("Error cleaning stray marker:", e); }
        }
        katexBuffer = '';
        return false;
    }

    const trimmedKatexBuffer = katexBuffer.trim();

    try {
        const stringToRender = trimmedKatexBuffer.length > 0 ? trimmedKatexBuffer : " ";
        const katexHtml = katex.renderToString(stringToRender, {
            displayMode: isDisplay,
            throwOnError: false,
            output: "html",
            strict: false
        });

        if (katexHtml.includes('katex-error') && trimmedKatexBuffer.length > 0) {
            console.warn(`[KaTeX Replace Marker] KaTeX reported a rendering error for buffer: "${trimmedKatexBuffer}". Marker ID: ${markerId}. Raw text will remain.`);
             try {
                 const marker = currentAnswerElement.querySelector(`span[data-katex-start-id="${markerId}"]`);
                 if (marker?.parentNode) marker.parentNode.removeChild(marker);
                 else console.warn("Couldn't find marker to clean up after render error.");
             } catch (cleanupError) { console.error("Error during marker cleanup after render error:", cleanupError); }
            katexBuffer = '';
            return false;
        }

        const startMarker = currentAnswerElement.querySelector(`span[data-katex-start-id="${markerId}"]`);

        if (!startMarker) {
            console.error(`[KaTeX Replace Marker] Error: Cannot find start marker span with ID: ${markerId}. Aborting replacement. Content might be inconsistent.`);
            katexBuffer = '';
            return false;
        }

        if (!startMarker.parentNode) {
             console.error(`[KaTeX Replace Marker] Error: Start marker span with ID ${markerId} found but has no parent (already removed?). Aborting.`);
             katexBuffer = '';
             return false;
        }

        const parent = startMarker.parentNode;
        let nodesToRemove = [];
        let currentNode = startMarker.nextSibling;

        while (currentNode) {
            nodesToRemove.push(currentNode);
            if (nodesToRemove.length > 1000) {
                console.error("[KaTeX Replace Marker] Error: Excessive node collection limit reached (1000). Aborting replacement.");
                 try { parent.removeChild(startMarker); } catch(e){}
                 katexBuffer = '';
                 return false;
            }
            currentNode = currentNode.nextSibling;
        }

        const katexSpan = document.createElement('span');
        katexSpan.setAttribute(KATEX_RENDERED_ATTR, 'true');
        katexSpan.innerHTML = katexHtml;

        parent.insertBefore(katexSpan, startMarker);
        parent.removeChild(startMarker);

        nodesToRemove.forEach(node => {
            if (node.parentNode === parent) {
                parent.removeChild(node);
            } else {
                console.warn("[KaTeX Replace Marker] Node parent changed or node removed unexpectedly during collection, skipping removal:", node);
            }
        });

        appendToAnswer(null, katexSpan);
        katexBuffer = '';
        return true;

    } catch (error) {
        console.error("[KaTeX Replace Marker] General error during marker-based replacement:", error);
        try {
            const marker = currentAnswerElement?.querySelector(`span[data-katex-start-id="${markerId}"]`);
            if (marker?.parentNode) marker.parentNode.removeChild(marker);
        } catch (cleanupError) { console.error("Error during marker cleanup after general error:", cleanupError); }
        katexBuffer = '';
        return false;
    }
}

function finalizeTurnOnErrorOrClose() {
    console.log("[DEBUG] finalizeTurnOnErrorOrClose called.");
    if (currentProcessingMode === MODE_INSIDE_CODE_BLOCK && currentCodeBlockElement) {
        console.warn("Stream ended unexpectedly inside code block. Finalizing highlight.");
        try { finalizeCodeBlock(true); } catch (e) { console.error("Prism highlight error on close:", e); }
    }
    if (currentProcessingMode === MODE_KATEX_BUFFERING_INLINE ||
        currentProcessingMode === MODE_KATEX_BUFFERING_DISPLAY ||
        currentProcessingMode === MODE_MAYBE_START_DISPLAY_KATEX) {
        console.warn("Stream ended unexpectedly inside KaTeX block. Processing raw text.");
        if (currentKatexMarkerId) {
            renderAndReplaceKatex(currentProcessingMode === MODE_KATEX_BUFFERING_DISPLAY, currentKatexMarkerId);
            currentKatexMarkerId = null;
        }
    }
     if (currentProcessingMode === MODE_INSIDE_THINK || currentProcessingMode === MODE_MAYBE_END_THINK || currentProcessingMode === MODE_SEEKING_TAG) {
         console.warn("Stream ended unexpectedly inside/seeking Think tags. Finishing think buffer:", thinkBuffer);
         if (currentThinkingArea && currentThinkingArea.style.display !== 'none' && currentThinkingPreElement) {
              appendRawTextToThinkingArea("\n--- (Stream ended unexpectedly during thinking) ---");
         }
     }

    formatAnswerBubbleFinal();
    resetStreamingState();
    setInputDisabledState(true);
}

/**
 * Creates the DOM structure for a new, live code block being streamed.
 * Hides the run button for unsupported languages.
 * @param {string} language - The language specified after the opening ``` fence.
 */
function createCodeBlockStructure(language) {
    if (!currentCodeBlocksArea) {
        console.error("createCodeBlockStructure: Code blocks area is null!");
        return;
    }
    codeBlockCounterThisTurn++;
    const currentCodeNumber = codeBlockCounterThisTurn;
    const blockId = `code-block-turn${currentTurnId}-${currentCodeNumber}`;
    const safeLanguage = (language || '').trim().toLowerCase() || 'plaintext';

    // Language aliases and Prism language determination (as before)
    const langAlias = { /* ... keep your existing aliases ... */
        'python': 'python', 'py': 'python', 'javascript': 'javascript', 'js': 'javascript',
        'html': 'markup', 'xml': 'markup', 'svg': 'markup', 'css': 'css', 'bash': 'bash',
        'sh': 'bash', 'shell': 'bash', 'json': 'json', 'yaml': 'yaml', 'yml': 'yaml',
        'markdown': 'markdown', 'md': 'markdown', 'sql': 'sql', 'java': 'java', 'c': 'c',
        'cpp': 'cpp', 'c++': 'cpp', 'csharp': 'csharp', 'cs': 'csharp', 'go': 'go',
        'rust': 'rust', 'php': 'php', 'ruby': 'ruby', 'rb': 'ruby',
        'dockerfile': 'docker', 'docker': 'docker', 'typescript': 'typescript', 'ts': 'typescript',
        'plaintext': 'plain', 'text': 'plain',
    };
    const prismLang = langAlias[safeLanguage] || safeLanguage;
    const displayLang = safeLanguage; // Use the cleaned-up language name for checks/display
    currentCodeBlockLang = prismLang; // Store the Prism language used

    // --- ADD: List of languages supported by the backend Docker execution ---
    const supportedExecutionLanguages = [
        "python", "javascript", "cpp", "csharp", "typescript", "java", "go", "rust"
        // Add or remove languages based on your config.py SUPPORTED_LANGUAGES keys
    ];
    const isLanguageSupported = supportedExecutionLanguages.includes(displayLang);
    // --- END OF ADD ---

    const playIconSvg = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;

    const container = document.createElement('div');
    container.classList.add('code-block-container');
    container.id = blockId;
    container.dataset.language = displayLang; // Store the display language

    const codeHeader = document.createElement('div');
    codeHeader.classList.add('code-block-header');

    const codeButtonsDiv = document.createElement('div');
    codeButtonsDiv.classList.add('code-block-buttons');

    // Create buttons (as before)
    const runStopBtn = document.createElement('button');
    runStopBtn.classList.add('run-code-btn', 'code-action-btn');
    runStopBtn.dataset.status = 'idle';
    runStopBtn.innerHTML = playIconSvg;
    runStopBtn.title = 'Run Code';
    runStopBtn.addEventListener('click', handleRunStopCodeClick);

    const toggleCodeBtn = document.createElement('button');
    toggleCodeBtn.classList.add('toggle-code-btn', 'code-action-btn');
    toggleCodeBtn.textContent = 'Hide';
    toggleCodeBtn.title = 'Show/Hide Code';

    const copyCodeBtn = document.createElement('button');
    copyCodeBtn.classList.add('copy-code-btn', 'code-action-btn');
    copyCodeBtn.textContent = 'Copy';
    copyCodeBtn.title = 'Copy Code';

    // --- ADD: Conditionally hide run button ---
    if (!isLanguageSupported) {
        runStopBtn.style.display = 'none'; // Hide the button entirely
        runStopBtn.title = `Run Code (language '${displayLang}' not supported for execution)`; // Update title even if hidden
        console.log(`Hiding run button for unsupported language: ${displayLang}`); // Optional log
    }
    // --- END OF ADD ---

    // Add buttons to their container (run button is added even if hidden, simplifies layout logic)
    codeButtonsDiv.appendChild(runStopBtn);
    codeButtonsDiv.appendChild(toggleCodeBtn);
    codeButtonsDiv.appendChild(copyCodeBtn);

    // Code block title (as before)
    const codeTitle = document.createElement('span');
    codeTitle.classList.add('code-block-title');
    codeTitle.textContent = `Code ${currentCodeNumber} (${displayLang})`;
    codeTitle.style.flexGrow = '1';
    codeTitle.style.textAlign = 'left';

    // Assemble header (as before)
    codeHeader.appendChild(codeButtonsDiv);
    codeHeader.appendChild(codeTitle);

    // Create pre/code elements (as before)
    const preElement = document.createElement('pre');
    preElement.classList.add('manual');
    const codeElement = document.createElement('code');
    codeElement.className = `language-${prismLang}`;
    codeElement.setAttribute('contenteditable', 'true'); // Live code is editable
    codeElement.setAttribute('spellcheck', 'false');

    // Assign to global state variables for streaming (as before)
    currentCodeBlockPreElement = preElement;
    currentCodeBlockElement = codeElement;

    // Create output area structure (as before)
    const outputHeader = document.createElement('div');
    outputHeader.classList.add('code-output-header');
    outputHeader.style.display = 'none';
    // ... (rest of output header setup: buttons, title, status span) ...
    const outputButtonsDiv = document.createElement('div');
    outputButtonsDiv.classList.add('code-block-buttons');
    const placeholderSpan = document.createElement('span');
    placeholderSpan.classList.add('output-header-button-placeholder');
    outputButtonsDiv.appendChild(placeholderSpan);
    const toggleOutputBtn = document.createElement('button');
    toggleOutputBtn.classList.add('toggle-output-btn', 'code-action-btn');
    toggleOutputBtn.textContent = 'Hide';
    toggleOutputBtn.title = 'Show/Hide Output';
    const copyOutputBtn = document.createElement('button');
    copyOutputBtn.classList.add('copy-output-btn', 'code-action-btn');
    copyOutputBtn.textContent = 'Copy';
    copyOutputBtn.title = 'Copy Output';
    outputButtonsDiv.appendChild(toggleOutputBtn);
    outputButtonsDiv.appendChild(copyOutputBtn);
    const outputTitle = document.createElement('span');
    outputTitle.classList.add('output-header-title');
    outputTitle.textContent = `Output Code ${currentCodeNumber}`;
    const codeStatusSpan = document.createElement('span');
    codeStatusSpan.classList.add('code-status-span');
    codeStatusSpan.textContent = 'Idle';
    outputHeader.appendChild(outputButtonsDiv);
    outputHeader.appendChild(outputTitle);
    outputHeader.appendChild(codeStatusSpan);

    const outputConsoleDiv = document.createElement('div');
    outputConsoleDiv.classList.add('code-output-console');
    outputConsoleDiv.style.display = 'none';
    const outputPre = document.createElement('pre');
    outputConsoleDiv.appendChild(outputPre);

    // Assemble the full block (as before)
    preElement.appendChild(codeElement);
    container.appendChild(codeHeader);
    container.appendChild(preElement);
    container.appendChild(outputHeader);
    container.appendChild(outputConsoleDiv);

    // Add event listeners (as before)
    // Toggle code listener
    toggleCodeBtn.addEventListener('click', () => {
        const isHidden = preElement.classList.toggle('hidden');
        toggleCodeBtn.textContent = isHidden ? 'Show' : 'Hide';
    });
    // Copy code listener
    copyCodeBtn.addEventListener('click', async () => {
        if (!codeElement) return;
        try {
            await navigator.clipboard.writeText(codeElement.textContent || '');
            copyCodeBtn.textContent = 'Copied!';
            copyCodeBtn.classList.add('copied');
            setTimeout(() => { copyCodeBtn.textContent = 'Copy'; copyCodeBtn.classList.remove('copied'); }, 1500);
        } catch (err) {
            console.error('Failed to copy code: ', err);
            copyCodeBtn.textContent = 'Error';
            setTimeout(() => { copyCodeBtn.textContent = 'Copy'; }, 1500);
        }
    });
    // Run button listener is already attached above
    // Debounced highlight listener for editable code (as before)
    const debouncedHighlight = debounce(() => {
        const savedPosition = getCursorPosition(codeElement);
        if (savedPosition === -1) { console.warn("Could not save cursor position or cursor not in element. Highlight may cause cursor jump."); }
        try {
            const currentText = codeElement.textContent;
            codeElement.innerHTML = ''; 
            codeElement.textContent = currentText; 
            Prism.highlightElement(codeElement);
            if (savedPosition !== -1) { setCursorPosition(codeElement, savedPosition); }
        } catch (e) {
            console.error("Error during debounced highlighting:", e);
            if (savedPosition !== -1) { setCursorPosition(codeElement, savedPosition); }
        }
    }, 500);
    codeElement.addEventListener('input', debouncedHighlight);
    codeElement.addEventListener('paste', (e) => { setTimeout(debouncedHighlight, 100); });
    // Output area listeners (as before)
    toggleOutputBtn.addEventListener('click', () => {
        const isHidden = outputConsoleDiv.classList.toggle('hidden');
        toggleOutputBtn.textContent = isHidden ? 'Show' : 'Hide';
    });
    copyOutputBtn.addEventListener('click', async () => {
        if (!outputPre) return;
        try {
            await navigator.clipboard.writeText(outputPre.textContent || '');
            copyOutputBtn.textContent = 'Copied!';
            copyOutputBtn.classList.add('copied');
            setTimeout(() => { copyOutputBtn.textContent = 'Copy'; copyOutputBtn.classList.remove('copied'); }, 1500);
        } catch (err) {
            console.error('Failed to copy output: ', err);
            copyOutputBtn.textContent = 'Error';
            setTimeout(() => { copyOutputBtn.textContent = 'Copy'; }, 1500);
        }
    });

    // Append to DOM and reset state (as before)
    currentCodeBlocksArea.appendChild(container);
    lastAppendedNode = null; // Reset last appended node for main answer bubble
}

async function handleRunStopCodeClick(event) {
    const button = event.currentTarget;
    const container = button.closest('.code-block-container');
    if (!container) return;

    const codeBlockId = container.id;
    const language = container.dataset.language;
    const codeElement = container.querySelector('code');
    const outputHeader = container.querySelector('.code-output-header');
    const outputConsoleDiv = container.querySelector('.code-output-console');
    const outputPre = outputConsoleDiv ? outputConsoleDiv.querySelector('pre') : null;
    const statusSpan = outputHeader ? outputHeader.querySelector('.code-status-span') : null;

    if (!language || !codeElement || !outputHeader || !outputConsoleDiv || !outputPre || !statusSpan) {
        console.error(`Missing elements for code block ${codeBlockId}`);
        addErrorMessage(`Cannot run code block ${codeBlockId}: Internal UI error.`);
        return;
    }

    const playIconSvg = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;
    const stopIconSvg = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><rect width="100" height="100" rx="15"/></svg>`;
    const stoppingIconSvg = `<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style="display: block;"><path d="M12 2V6M12 18V22M6 12H2M22 12H18M19.0711 4.92893L16.2426 7.75736M7.75736 16.2426L4.92893 19.0711M19.0711 19.0711L16.2426 16.2426M7.75736 7.75736L4.92893 4.92893" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

    const code = codeElement.textContent || '';
    const currentStatus = button.dataset.status;

    if (!websocket || websocket.readyState !== WebSocket.OPEN) {
        console.warn("WebSocket not open. Cannot run/stop code.");
        statusSpan.textContent = 'Error: Disconnected';
        statusSpan.className = 'code-status-span error';
        outputHeader.style.display = 'flex';
        addErrorMessage("Cannot run/stop code: Not connected to server.");
        return;
    }

    if (currentStatus === 'idle') {
        console.log(`Requesting run for block ${codeBlockId} (${language})`);
        button.dataset.status = 'running';
        button.innerHTML = stopIconSvg;
        button.title = 'Stop Execution';

        outputPre.innerHTML = '';
        outputHeader.style.display = 'flex';
        outputConsoleDiv.style.display = 'block';
        outputConsoleDiv.classList.remove('hidden');
        const toggleOutputBtn = outputHeader.querySelector('.toggle-output-btn');
        if (toggleOutputBtn) toggleOutputBtn.textContent = 'Hide';

        statusSpan.textContent = 'Running...';
        statusSpan.className = 'code-status-span running';

        websocket.send(JSON.stringify({
            type: 'run_code',
            payload: { code_block_id: codeBlockId, language: language, code: code }
        }));

    } else if (currentStatus === 'running') {
        console.log(`Requesting stop for block ${codeBlockId}`);
        button.dataset.status = 'stopping';
        button.innerHTML = stoppingIconSvg;
        button.title = 'Stopping...';
        button.disabled = true;

        statusSpan.textContent = 'Stopping...';
        statusSpan.className = 'code-status-span stopping';

        websocket.send(JSON.stringify({
            type: 'stop_code',
            payload: { code_block_id: codeBlockId }
        }));
    } else if (currentStatus === 'stopping') {
        console.log(`Already stopping block ${codeBlockId}`);
    }
}

function addCodeOutput(outputPreElement, streamType, text) {
    if (!outputPreElement || !text) return;

    const span = document.createElement('span');
    span.classList.add(streamType === 'stderr' ? 'stderr-output' : 'stdout-output');
    span.textContent = text;
    outputPreElement.appendChild(span);
    outputPreElement.scrollTop = outputPreElement.scrollHeight;
}

/**
 * Appends text content to the currently active code block element 
 * and triggers debounced syntax highlighting.
 * @param {string} text - The text chunk to append.
 */
function appendToCodeBlock(text) {
    // Ensure we have a target code element to append to
    if (currentCodeBlockElement) {
        // Append the raw text node to the <code> element
        currentCodeBlockElement.appendChild(document.createTextNode(text));
        
        // Auto-scroll the code block's <pre> container if it's visible
        // currentCodeBlockPreElement is the parent <pre> element
        if(currentCodeBlockPreElement && !currentCodeBlockPreElement.classList.contains('hidden')) {
            // Check if the scroll position is near the bottom
            const isNearCodeBottom = currentCodeBlockPreElement.scrollHeight - currentCodeBlockPreElement.scrollTop - currentCodeBlockPreElement.clientHeight < 50; // Threshold of 50px
            if(isNearCodeBottom) {
                // Use requestAnimationFrame for smoother scrolling after the DOM update
                requestAnimationFrame(() => { 
                    // Check again inside animation frame as state might change rapidly
                    if(currentCodeBlockPreElement) { 
                       currentCodeBlockPreElement.scrollTop = currentCodeBlockPreElement.scrollHeight; 
                    }
                });
            }
        }

        // --- THIS IS THE SPECIFIC CHANGE FOR THIS STEP ---
        // Trigger the debounced highlighter every time text is appended
        throttledStreamHighlight()
        // --- END OF SPECIFIC CHANGE ---

    } else {
        // Log an error if we try to append but no code block is active
        console.error("Attempted to append to null code block element!");
    }
}

/**
 * Appends text or a DOM node to the current AI's answer area.
 * Targets the specific content div within the answer bubble.
 * @param {string | null} text - Text to append.
 * @param {Node | null} node - DOM node to append.
 */
function appendToAnswer(text = null, node = null) {
    if (!currentAnswerElement) {
        console.error("[appendToAnswer] currentAnswerElement is null. Cannot append.");
        return;
    }

    // **MODIFIED**: Target the dedicated content area within the AI message bubble
    const targetContentArea = currentAnswerElement.querySelector('.live-ai-content-area');
    if (!targetContentArea) {
        console.error("[appendToAnswer] '.live-ai-content-area' not found in currentAnswerElement. Appending to currentAnswerElement directly as fallback.");
        // Fallback to old behavior if somehow the structure is missing, though this shouldn't happen with the updated setupNewAiTurn
        const fallbackTarget = currentAnswerElement; 
        
        // Logic from original appendToAnswer, but using fallbackTarget
        let isMeaningfulContentFallback = (text && text.trim().length > 0) ||
                                   (node && node.nodeType !== Node.COMMENT_NODE && (!node.textContent || node.textContent.trim().length > 0));

        if (!firstAnswerTokenReceived && isMeaningfulContentFallback) {
            if (fallbackTarget.style.display === 'none') {
                fallbackTarget.style.display = '';
            }
            const loadingDotsFallback = fallbackTarget.querySelector('.loading-dots');
            if (loadingDotsFallback) {
                loadingDotsFallback.remove();
            }
            firstAnswerTokenReceived = true;
        }

        if (node) {
            if (!node.classList || !node.classList.contains('loading-dots')) {
                fallbackTarget.appendChild(node);
                lastAppendedNode = node; // Keep track of last appended node relative to its parent
            }
        } else if (text !== null && text.length > 0) {
            accumulatedAnswerText += text; // Still accumulate globally for formatAnswerBubbleFinal
            if (lastAppendedNode && lastAppendedNode.nodeType === Node.TEXT_NODE && lastAppendedNode.parentNode === fallbackTarget) {
                lastAppendedNode.nodeValue += text;
            } else {
                const textNode = document.createTextNode(text);
                fallbackTarget.appendChild(textNode);
                lastAppendedNode = textNode;
            }
        }
        return; // End of fallback logic
    }


    // --- Main logic using targetContentArea ---
    let isMeaningfulContent = (text && text.trim().length > 0) ||
                              (node && node.nodeType !== Node.COMMENT_NODE && (!node.textContent || node.textContent.trim().length > 0));

    if (!firstAnswerTokenReceived && isMeaningfulContent) {
        // This logic primarily handles making the *overall* currentAnswerElement visible
        // if it was hidden (e.g., due to thinkingRequestedForCurrentTurn).
        // The loading dots are inside targetContentArea.
        if (currentAnswerElement.style.display === 'none') {
            currentAnswerElement.style.display = ''; // Show the whole bubble
        }
        const loadingDots = targetContentArea.querySelector('.loading-dots');
        if (loadingDots) {
            loadingDots.remove();
        }
        firstAnswerTokenReceived = true;
    }

    if (node) {
        // Don't append loading dots if they are the node (they are handled above)
        if (!node.classList || !node.classList.contains('loading-dots')) {
            targetContentArea.appendChild(node);
            // lastAppendedNode should refer to nodes within targetContentArea for text concatenation logic
            lastAppendedNode = (targetContentArea.contains(node)) ? node : null;
        }
    } else if (text !== null && text.length > 0) {
        accumulatedAnswerText += text; // Global accumulation for final formatting
        // Smart text node concatenation within targetContentArea
        if (lastAppendedNode && lastAppendedNode.nodeType === Node.TEXT_NODE && lastAppendedNode.parentNode === targetContentArea) {
            lastAppendedNode.nodeValue += text;
        } else {
            const textNode = document.createTextNode(text);
            targetContentArea.appendChild(textNode);
            lastAppendedNode = textNode;
        }
    }
}


/**
 * Finalizes the currently active code block, performing a final, 
 * non-debounced syntax highlight to ensure completeness.
 * Resets code block streaming state variables.
 * @param {boolean} isTruncated - Indicates if the stream ended unexpectedly (e.g., via EOS).
 */
function finalizeCodeBlock(isTruncated = false) {
    // Check if there is an active code block element being processed
    if (currentCodeBlockElement) {
        const blockContainer = currentCodeBlockElement.closest('.code-block-container');
        const blockId = blockContainer ? blockContainer.id : 'unknown';
        const langClass = currentCodeBlockElement.className; // e.g., "language-python"
        
        // Log the finalization attempt
        console.log(`[finalizeCodeBlock] Finalizing highlight for block ${blockId} (lang class: ${langClass}). Stream truncated: ${isTruncated}`);

        try {
            // Normalizing the text nodes within the <code> element can sometimes help 
            // Prism handle unusual spacing or fragmented text nodes correctly.
            currentCodeBlockElement.normalize(); 
            
            // --- THIS IS THE SPECIFIC CHANGE FOR THIS STEP ---
            // Perform an immediate, final highlight on the entire code block element.
            // This ensures the complete code is highlighted, catching any parts potentially 
            // missed by the last debounced call if the stream ended abruptly.
            if (typeof Prism !== 'undefined' && typeof Prism.highlightElement === 'function') {
                Prism.highlightElement(currentCodeBlockElement);
                console.log(`[finalizeCodeBlock] Final highlighting applied successfully for ${blockId}.`);
            } else {
                 console.warn(`[finalizeCodeBlock] Prism.js or highlightElement not available for final highlight on block ${blockId}.`);
            }
            // --- END OF SPECIFIC CHANGE ---

            // Optional: Cancel any pending debounced highlight call.
            // This requires your debounce implementation to have a .cancel() method.
            // If it doesn't, you can omit this block.
            // if (debouncedStreamHighlight && typeof debouncedStreamHighlight.cancel === 'function') {
            //     console.log(`[finalizeCodeBlock] Cancelling pending debounced highlight for block ${blockId}.`);
            //     debouncedStreamHighlight.cancel();
            // }

        } catch (e) {
            // Log any errors during the final highlighting process
            console.error(`Prism highlight error on finalizeCodeBlock (lang '${currentCodeBlockLang}', block ${blockId}):`, e);
        }
    } else {
         // Log a warning if this function is called when no code block is active
         console.warn("[finalizeCodeBlock] Called but currentCodeBlockElement is null.");
    }
    
    // Reset the global state variables related to the code block stream AFTER processing.
    currentCodeBlockElement = null;    // Reference to the <code> element
    currentCodeBlockPreElement = null; // Reference to the parent <pre> element
    currentCodeBlockLang = '';         // Language identifier (e.g., 'python')
    // fenceBuffer should also be reset if it's tracked globally and related
    // fenceBuffer = ''; 
}


function resetAllCodeButtonsOnErrorOrClose() {
    console.log("Resetting all code run/stop buttons and statuses due to connection issue.");
    const playIconSvg = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;

    document.querySelectorAll('.code-block-container').forEach(container => {
        const button = container.querySelector('.run-code-btn');
        const outputHeader = container.querySelector('.code-output-header');
        const statusSpan = outputHeader ? outputHeader.querySelector('.code-status-span') : null;

        if (button && button.dataset.status !== 'idle') {
            button.dataset.status = 'idle';
            button.innerHTML = playIconSvg;
            button.title = 'Run Code';
            button.disabled = false;
        }
        if (statusSpan) {
             if (!statusSpan.classList.contains('idle') && !statusSpan.classList.contains('success')) {
                 if (outputHeader) outputHeader.style.display = 'flex';
                 statusSpan.textContent = 'Error: Disconnected';
                 statusSpan.className = 'code-status-span error';
             }
        }
    });
}

function connectWebSocket() {

    let sessionId = null;
    const pathParts = window.location.pathname.split('/');
    // Example: /chat/ef7a41e6-2ba7-4882-b9bb-91c03edb25ac
    // pathParts would be ["", "chat", "ef7a41e6-2ba7-4882-b9bb-91c03edb25ac"]
    if (pathParts.length >= 3 && pathParts[1] === 'chat') {
        sessionId = pathParts[2]; // This should be the session_id
        if (!sessionId || sessionId.trim() === "") {
             console.error("Session ID extracted from path is empty.");
             sessionId = null; // Treat empty ID as invalid
        }
    }

    if (!sessionId) {
        console.error("Could not extract valid session ID from URL path:", window.location.pathname);
        // Use your existing addErrorMessage function if available
        if (typeof addErrorMessage === 'function') {
             addErrorMessage("Cannot connect to chat: Invalid session ID in URL.");
        } else {
             alert("Cannot connect to chat: Invalid session ID in URL.");
        }
        // Use your existing setInputDisabledState function if available
        if (typeof setInputDisabledState === 'function') {
             setInputDisabledState(true);
        }
        return; // Stop connection attempt
    }
    // --- END: Added logic to get session ID from URL path ---

    // Use your existing global clientId variable
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // Construct the URL with the extracted sessionId and existing clientId
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/${sessionId}/${clientId}`; // Corrected URL format

    console.log(`[DEBUG] Attempting to connect to WebSocket: ${wsUrl}`);

    const playIconSvg = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`; // Keep this if used elsewhere, otherwise remove

    try {
        // Use the corrected wsUrl
        const ws = new WebSocket(wsUrl);
        console.log('connectWebSocket: Creating WebSocket object...');

        ws.onopen = (event) => {
            console.log("[DEBUG] WebSocket connection opened. ws object:", ws);
            websocket = ws; // Assign to your global websocket variable

            // Check for your actual helper functions
            if (typeof addSystemMessage !== 'function' || typeof setupNewAiTurn !== 'function' || typeof appendToAnswer !== 'function' || typeof formatAnswerBubbleFinal !== 'function' || typeof resetStreamingState !== 'function' || typeof setInputDisabledState !== 'function') {
                console.error("CRITICAL ERROR: One or more required helper functions are not defined when ws.onopen is called. Check script order.");
                alert("Chat initialization failed. Please refresh the page. (Error: Helpers undefined)");
                if (typeof setInputDisabledState === 'function') setInputDisabledState(true);
                return;
            }

            if (typeof setInputDisabledState === 'function') setInputDisabledState(false);
            if (typeof addSystemMessage === 'function') addSystemMessage("Connected to the chat server.");

            // thinkingRequestedForCurrentTurn = false; // Assuming this is a global/accessible variable
            // setupNewAiTurn(); // Your existing function
            // const welcomeMessage = "Hello! How can I help you today?";
            // appendToAnswer(welcomeMessage); // Your existing function
            // formatAnswerBubbleFinal(); // Your existing function
            // console.log("[DEBUG ws.onopen] Resetting state after welcome message.");
            // resetStreamingState(); // Your existing function

            if(messageInput && messageInput.offsetParent !== null) messageInput.focus();
        };

        ws.onmessage = (event) => {
            // This is the full onmessage logic from your provided script
            let isJsonMessage = false;
            let messageData = null;
            try {
                if (typeof event.data === 'string' && event.data.startsWith('{')) {
                    messageData = JSON.parse(event.data);
                    if (messageData && messageData.type && messageData.payload && messageData.payload.code_block_id) {
                        isJsonMessage = true;
                    }
                }
            } catch (e) {
                isJsonMessage = false;
            }

            if (isJsonMessage) {
                // console.log("%c[ws.onmessage] Code Execution JSON received:", "color: blue;", messageData);
                const { type, payload } = messageData;
                const { code_block_id } = payload;
                const container = document.getElementById(code_block_id);

                if (!container) {
                    console.warn(`Received message for unknown code block ID: ${code_block_id}`, payload);
                    return;
                }

                const outputHeader = container.querySelector('.code-output-header');
                const outputConsoleDiv = container.querySelector('.code-output-console');
                const outputPre = outputConsoleDiv ? outputConsoleDiv.querySelector('pre') : null;
                const runStopBtn = container.querySelector('.run-code-btn');
                const statusSpan = outputHeader ? outputHeader.querySelector('.code-status-span') : null;

                if (!outputHeader || !outputPre || !runStopBtn || !statusSpan) {
                        console.error(`Missing elements for code block ${code_block_id}. Cannot process message.`);
                        return;
                }

                switch (type) {
                    case 'code_output':
                        const { stream, data } = payload;
                        if (outputHeader.style.display === 'none') {
                            // console.log(`Showing output header for ${code_block_id} on first output.`);
                            outputHeader.style.display = 'flex';
                        }
                        if (outputConsoleDiv.style.display === 'none') {
                            // console.log(`Showing output console for ${code_block_id} on first output.`);
                            outputConsoleDiv.style.display = 'block';
                            outputConsoleDiv.classList.remove('hidden');
                                const toggleOutputBtn = outputHeader.querySelector('.toggle-output-btn');
                                if (toggleOutputBtn) toggleOutputBtn.textContent = 'Hide';
                        }
                        if (runStopBtn.dataset.status === 'idle'){
                                const stopIconSvg = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><rect width="100" height="100" rx="15"/></svg>`;
                                console.warn(`Received code output for ${code_block_id} while button was idle. Forcing state to running.`);
                                runStopBtn.dataset.status = 'running';
                                runStopBtn.innerHTML = stopIconSvg;
                                runStopBtn.title = 'Stop Execution';
                                runStopBtn.disabled = false;
                                statusSpan.textContent = 'Running...';
                                statusSpan.className = 'code-status-span running';
                        }
                        addCodeOutput(outputPre, stream, data); // Your existing function
                        break;

                    case 'code_finished':
                        if (outputHeader.style.display === 'none') {
                            // console.log(`Showing output header for ${code_block_id} on finish.`);
                            outputHeader.style.display = 'flex';
                        }
                            if (outputConsoleDiv.style.display === 'none') {
                            // console.log(`Showing output console for ${code_block_id} on finish.`);
                            outputConsoleDiv.style.display = 'block';
                            outputConsoleDiv.classList.remove('hidden');
                                const toggleOutputBtn = outputHeader.querySelector('.toggle-output-btn');
                                if (toggleOutputBtn) toggleOutputBtn.textContent = 'Hide';
                        }

                        const { exit_code, error } = payload;
                        let finishMessage = '';
                        let statusClass = '';

                        if (error) {
                            if (error === "Execution stopped by user.") {
                                finishMessage = 'Stopped'; statusClass = 'stopped';
                            } else if (error.startsWith("Execution timed out")) {
                                    finishMessage = 'Timeout'; statusClass = 'error';
                            } else {
                                    finishMessage = `Error (${exit_code !== undefined ? exit_code : 'N/A'})`; statusClass = 'error';
                                    console.error(`Execution error for ${code_block_id}:`, error);
                            }
                        } else {
                            finishMessage = `Finished (Exit: ${exit_code})`;
                            statusClass = exit_code === 0 ? 'success' : 'error';
                        }

                        statusSpan.textContent = finishMessage;
                        statusSpan.className = `code-status-span ${statusClass}`;

                        runStopBtn.dataset.status = 'idle';
                        // Use the playIconSvg defined earlier or redefine it if needed
                        runStopBtn.innerHTML = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;
                        runStopBtn.title = 'Run Code';
                        runStopBtn.disabled = false;
                        break;

                    default:
                        console.warn(`Received unknown code execution message type: ${type}`, payload);
                }

            } else {
                // console.log("%c[ws.onmessage] RAW chat data received:", "color: magenta;", event.data);
                let chunk = event.data;
                const currentTurnIdForMsg = currentTurnId; // Assuming currentTurnId is global/accessible

                // Inside your connectWebSocket -> ws.onmessage -> else (raw chat data) -> if (chunk === "<EOS>") block:

                if (chunk === "<EOS>") {
                    const currentTurnIdForMsg = currentTurnId; // Capture currentTurnId for logging context
                    console.log(`%c[ws.onmessage] Turn ${currentTurnIdForMsg}: Received <EOS>. Finalizing turn.`, 'color: green; font-weight: bold;');

                    // Handle cases where EOS is received unexpectedly in the middle of processing
                    if (currentProcessingMode === MODE_INSIDE_CODE_BLOCK) {
                        console.warn(`Turn ${currentTurnIdForMsg}: <EOS> received unexpectedly inside a code block. Appending any fence buffer and finalizing code block.`);
                        if (fenceBuffer && fenceBuffer.length > 0) {
                            appendToCodeBlock(fenceBuffer); // Append any partial fence characters
                        }
                        try {
                            finalizeCodeBlock(true); // true indicates truncation
                        } catch (e) {
                            console.error(`Error finalizing code block on EOS for Turn ${currentTurnIdForMsg}:`, e);
                        }
                    } else if (currentProcessingMode === MODE_SEEKING_CODE_END_FENCE) {
                        console.warn(`Turn ${currentTurnIdForMsg}: <EOS> received unexpectedly while seeking code end fence. Treating fence buffer '${fenceBuffer}' as part of the code.`);
                        appendToCodeBlock(fenceBuffer); // Treat buffered fence characters as code content
                        try {
                            finalizeCodeBlock(true); // true indicates truncation
                        } catch (e) {
                            console.error(`Error finalizing code block on EOS (seeking end fence) for Turn ${currentTurnIdForMsg}:`, e);
                        }
                    } else if (currentProcessingMode === MODE_SEEKING_CODE_LANGUAGE && langBuffer && langBuffer.length > 0) {
                        console.warn(`Turn ${currentTurnIdForMsg}: <EOS> received unexpectedly while seeking code language. Treating '${FENCE}${langBuffer}' as plain text.`);
                        appendToAnswer(FENCE + langBuffer); // Append the incomplete fence and language buffer as text
                    } else if (currentProcessingMode === MODE_SEEKING_CODE_START_FENCE && fenceBuffer && fenceBuffer.length > 0) {
                        console.warn(`Turn ${currentTurnIdForMsg}: <EOS> received unexpectedly while seeking code start fence. Treating '${fenceBuffer}' as plain text.`);
                        appendToAnswer(fenceBuffer); // Append the incomplete fence buffer as text
                    } else if (currentProcessingMode === MODE_KATEX_BUFFERING_INLINE || currentProcessingMode === MODE_KATEX_BUFFERING_DISPLAY) {
                        console.warn(`Turn ${currentTurnIdForMsg}: <EOS> received unexpectedly inside a KaTeX block. Attempting to render buffered content: "${katexBuffer}"`);
                        if (currentKatexMarkerId) {
                            renderAndReplaceKatex(currentProcessingMode === MODE_KATEX_BUFFERING_DISPLAY, currentKatexMarkerId);
                            currentKatexMarkerId = null; // Reset marker ID
                        } else {
                             // If no marker, append raw buffer as text to avoid losing it
                            appendToAnswer((currentProcessingMode === MODE_KATEX_BUFFERING_DISPLAY ? "$$" : "$") + katexBuffer);
                        }
                    } else if (currentProcessingMode === MODE_MAYBE_START_DISPLAY_KATEX) {
                        console.warn(`Turn ${currentTurnIdForMsg}: <EOS> received unexpectedly after a '$'. Treating as plain text.`);
                        appendToAnswer('$'); // Append the single dollar sign
                    } else if (currentProcessingMode === MODE_INSIDE_THINK || currentProcessingMode === MODE_MAYBE_END_THINK || currentProcessingMode === MODE_SEEKING_TAG) {
                        console.warn(`Turn ${currentTurnIdForMsg}: <EOS> received unexpectedly while in mode '${currentProcessingMode}'. Buffered tag: "${tagBuffer}", Buffered think: "${thinkBuffer}"`);
                        if (thinkingRequestedForCurrentTurn && currentThinkingArea && currentThinkingPreElement) {
                            if (tagBuffer) appendRawTextToThinkingArea(tagBuffer); // Append any partial tag
                            // thinkBuffer should have already been appended incrementally by appendRawTextToThinkingArea
                            appendRawTextToThinkingArea("\n--- (End of stream during thinking process) ---");
                        } else if (tagBuffer) { // If not in thinking mode but had a tag buffer
                            appendToAnswer(tagBuffer);
                        }
                    }
                    // Any other modes might just proceed to formatAnswerBubbleFinal with existing accumulatedAnswerText

                    formatAnswerBubbleFinal(); // Process and finalize the main answer content

                    // Add Timestamp to the live AI answer bubble
                    if (currentAnswerElement) {
                        const timestampElem = document.createElement('p');
                        timestampElem.classList.add('text-xs', 'text-slate-500', 'mt-1', 'text-left'); // Consistent styling
                        timestampElem.textContent = new Date().toLocaleString(); // Time when EOS is received
                        currentAnswerElement.appendChild(timestampElem); // Append to the main bubble, after content div
                    }

                    resetStreamingState(); // Reset all streaming state variables for the next turn
                    
                    // Re-enable input fields
                    if (typeof setInputDisabledState === 'function') {
                        setInputDisabledState(false);
                    }
                    
                    // Focus on the message input if it's visible
                    if (messageInput && messageInput.offsetParent !== null) {
                        messageInput.focus();
                    }
                    
                    // Scroll to the bottom of the chat history
                    setTimeout(() => scrollToBottom('smooth'), 50); 
                    
                    return; // Important to exit after handling EOS
                }

                if (chunk.startsWith("<ERROR>")) {
                    const errorMessage = chunk.substring(7);
                    console.error(`[ws.onmessage] Turn ${currentTurnIdForMsg}: Received <ERROR>:`, errorMessage);
                    if (typeof addErrorMessage === 'function') addErrorMessage(errorMessage);
                    finalizeTurnOnErrorOrClose(); // Your existing function
                    resetAllCodeButtonsOnErrorOrClose(); // Your existing function
                    setTimeout(() => scrollToBottom('smooth'), 50);
                    return;
                }

                if (chunk.length === 0) {
                        return;
                }

                if (!currentAiTurnContainer) { // Assuming global/accessible
                    if (chunk.trim().length > 0) {
                        console.warn(`%c[ws.onmessage] Turn ${currentTurnIdForMsg}: First non-empty chat chunk received, but turn container not set up? Forcing setup.`, 'color: red; font-weight: bold;');
                        setupNewAiTurn(); // Your existing function
                    } else {
                        return;
                    }
                }

                // Your existing complex state machine logic to process the chunk
                let currentPos = 0;
                while (currentPos < chunk.length) {
                    const char = chunk[currentPos];
                    let incrementPos = true;
                    let previousMode = currentProcessingMode; // Assuming global/accessible

                    if (char === '\\' && currentPos + 1 < chunk.length) {
                        const nextChar = chunk[currentPos + 1];
                        const escapableChars = '$`*\\<>';
                        if (escapableChars.includes(nextChar)) {
                            if (currentProcessingMode === MODE_INSIDE_THINK) { appendRawTextToThinkingArea(nextChar); }
                            else if (currentProcessingMode === MODE_KATEX_BUFFERING_INLINE || currentProcessingMode === MODE_KATEX_BUFFERING_DISPLAY) { katexBuffer += nextChar; appendToAnswer(nextChar); } // Assuming katexBuffer is global/accessible
                            else if (currentProcessingMode === MODE_INSIDE_CODE_BLOCK) { appendToCodeBlock(nextChar); } // Your existing function
                            else { appendToAnswer(nextChar); } // Your existing function
                            currentPos += 2;
                            incrementPos = false;
                            continue;
                        }
                    }

                    switch (currentProcessingMode) { // Assuming global/accessible
                        case MODE_ANSWER:
                            if (char === FENCE[0]) {
                                if (previousMode === MODE_MAYBE_START_DISPLAY_KATEX) { appendToAnswer('$'); }
                                katexBuffer = ''; currentKatexMarkerId = null;
                                fenceBuffer = char; // Assuming fenceBuffer is global/accessible
                                currentProcessingMode = MODE_SEEKING_CODE_START_FENCE;
                            } else if (char === '$') {
                                if (previousMode === MODE_MAYBE_START_DISPLAY_KATEX) { appendToAnswer('$'); }
                                currentProcessingMode = MODE_MAYBE_START_DISPLAY_KATEX;
                            } else if (char === '<') {
                                    if (previousMode === MODE_MAYBE_START_DISPLAY_KATEX) { appendToAnswer('$'); }
                                tagBuffer = char; // Assuming tagBuffer is global/accessible
                                currentProcessingMode = MODE_SEEKING_TAG;
                            } else {
                                if (previousMode === MODE_MAYBE_START_DISPLAY_KATEX) { appendToAnswer('$'); }
                                appendToAnswer(char);
                            }
                            break;
                        // ... include all your other cases from the state machine ...
                        case MODE_SEEKING_CODE_START_FENCE:
                            if (char === FENCE[fenceBuffer.length]) {
                                fenceBuffer += char;
                                if (fenceBuffer === FENCE) {
                                    fenceBuffer = ''; langBuffer = ''; // Assuming langBuffer is global/accessible
                                    currentProcessingMode = MODE_SEEKING_CODE_LANGUAGE;
                                }
                            } else {
                                appendToAnswer(fenceBuffer);
                                fenceBuffer = '';
                                currentProcessingMode = MODE_ANSWER;
                                incrementPos = false;
                            }
                            break;
                        case MODE_SEEKING_CODE_LANGUAGE:
                                if (char === '\n') {
                                    currentCodeBlockLang = langBuffer.trim(); // Assuming global/accessible
                                    createCodeBlockStructure(currentCodeBlockLang); // Your existing function
                                    appendCodeReference(); // Your existing function
                                    langBuffer = '';
                                    currentProcessingMode = MODE_INSIDE_CODE_BLOCK;
                                } else if (langBuffer.length > 50) {
                                    console.warn(`[State Machine] Turn ${currentTurnIdForMsg}: Code language line too long. Treating as text.`);
                                    appendToAnswer(FENCE + langBuffer + char);
                                    langBuffer = ''; fenceBuffer = '';
                                    currentProcessingMode = MODE_ANSWER;
                                } else {
                                    langBuffer += char;
                                }
                                break;
                        case MODE_INSIDE_CODE_BLOCK:
                            if (char === FENCE[0]) {
                                fenceBuffer = char;
                                currentProcessingMode = MODE_SEEKING_CODE_END_FENCE;
                            } else {
                                appendToCodeBlock(char);
                            }
                            break;
                        case MODE_SEEKING_CODE_END_FENCE:
                            if (char === FENCE[fenceBuffer.length]) {
                                fenceBuffer += char;
                                if (fenceBuffer === FENCE) {
                                    finalizeCodeBlock(); // Your existing function
                                    fenceBuffer = '';
                                    currentProcessingMode = MODE_ANSWER;
                                    lastAppendedNode = null; // Assuming global/accessible
                                    if (currentPos + 1 < chunk.length && chunk[currentPos + 1] === '\n') {
                                        currentPos++;
                                        incrementPos = true;
                                    }
                                }
                            } else {
                                appendToCodeBlock(fenceBuffer);
                                fenceBuffer = '';
                                currentProcessingMode = MODE_INSIDE_CODE_BLOCK;
                                incrementPos = false;
                            }
                            break;
                        case MODE_MAYBE_START_DISPLAY_KATEX:
                            if (char === '$') {
                                currentKatexMarkerId = `katex-marker-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
                                const startMarkerDisp = document.createElement('span');
                                startMarkerDisp.setAttribute('data-katex-start-id', currentKatexMarkerId);
                                startMarkerDisp.style.display = 'none';
                                appendToAnswer(null, startMarkerDisp);
                                appendToAnswer('$$');
                                currentProcessingMode = MODE_KATEX_BUFFERING_DISPLAY;
                                katexBuffer = '';
                            } else {
                                currentKatexMarkerId = `katex-marker-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
                                const startMarkerInline = document.createElement('span');
                                startMarkerInline.setAttribute('data-katex-start-id', currentKatexMarkerId);
                                startMarkerInline.style.display = 'none';
                                appendToAnswer(null, startMarkerInline);
                                appendToAnswer('$');
                                appendToAnswer(char);
                                currentProcessingMode = MODE_KATEX_BUFFERING_INLINE;
                                katexBuffer = char;
                            }
                            break;
                        case MODE_KATEX_BUFFERING_INLINE:
                            if (char === '$') {
                                appendToAnswer('$');
                                if (currentKatexMarkerId) { renderAndReplaceKatex(false, currentKatexMarkerId); currentKatexMarkerId = null; }
                                else { console.error(`Attempted end inline KaTeX Turn ${currentTurnIdForMsg} no marker ID!`); }
                                currentProcessingMode = MODE_ANSWER;
                            } else { katexBuffer += char; appendToAnswer(char); }
                            break;
                        case MODE_KATEX_BUFFERING_DISPLAY:
                                if (char === '$' && currentPos + 1 < chunk.length && chunk[currentPos + 1] === '$') {
                                    appendToAnswer('$$');
                                    if (currentKatexMarkerId) { renderAndReplaceKatex(true, currentKatexMarkerId); currentKatexMarkerId = null; }
                                    else { console.error(`Attempted end display KaTeX Turn ${currentTurnIdForMsg} no marker ID!`); }
                                    currentProcessingMode = MODE_ANSWER; currentPos++;
                                } else { katexBuffer += char; appendToAnswer(char); }
                                break;
                        case MODE_SEEKING_TAG:
                            tagBuffer += char;
                            const lowerTag = tagBuffer.toLowerCase();
                            if (lowerTag === THINK_START_TAG) {
                                thinkBuffer = ''; currentProcessingMode = MODE_INSIDE_THINK; tagBuffer = ''; lastAppendedNode = null; // Assuming thinkBuffer is global/accessible
                            } else if (THINK_START_TAG.startsWith(lowerTag)) { /* Keep buffering */ }
                            else {
                                appendToAnswer(tagBuffer); currentProcessingMode = MODE_ANSWER; tagBuffer = ''; incrementPos = false;
                            }
                            if (tagBuffer.length > 20) {
                                    console.warn(`[State Machine] Turn ${currentTurnIdForMsg}: Tag buffer excessive, treating as text: ${tagBuffer}`);
                                    appendToAnswer(tagBuffer); currentProcessingMode = MODE_ANSWER; tagBuffer = ''; incrementPos = false;
                            }
                            break;
                        case MODE_INSIDE_THINK:
                                if (char === '<') {
                                    tagBuffer = char; currentProcessingMode = MODE_MAYBE_END_THINK;
                                } else { appendRawTextToThinkingArea(char); } // Your existing function
                                break;
                        case MODE_MAYBE_END_THINK:
                                tagBuffer += char;
                                const lowerEndTag = tagBuffer.toLowerCase();
                                if (lowerEndTag === THINK_END_TAG) {
                                    thinkBuffer = ''; tagBuffer = ''; currentProcessingMode = MODE_ANSWER; lastAppendedNode = null;
                                } else if (THINK_END_TAG.startsWith(lowerEndTag)) { /* Keep buffering */ }
                                else {
                                    appendRawTextToThinkingArea(tagBuffer);
                                    currentProcessingMode = MODE_INSIDE_THINK; tagBuffer = ''; incrementPos = false;
                                }
                                if (tagBuffer.length > 20) {
                                        console.warn(`[State Machine] Turn ${currentTurnIdForMsg}: End tag buffer excessive, treating as think text: ${tagBuffer}`);
                                        appendRawTextToThinkingArea(tagBuffer); currentProcessingMode = MODE_INSIDE_THINK; tagBuffer = ''; incrementPos = false;
                                }
                                break;
                        default:
                            console.error(`Unknown processing mode: ${currentProcessingMode} in Turn ${currentTurnIdForMsg}`);
                            appendToAnswer(char);
                            currentProcessingMode = MODE_ANSWER;
                    }

                    if (incrementPos) { currentPos++; }
                }
                scrollToBottom(); // Your existing function
            }
        };

        ws.onerror = (event) => {
            // This is the full onerror logic from your provided script
            console.error("WebSocket error observed:", event);
            if (typeof addErrorMessage === 'function') addErrorMessage("WebSocket connection error. Please check the server or try refreshing. See console for details.");
            if (typeof finalizeTurnOnErrorOrClose === 'function') finalizeTurnOnErrorOrClose();
            if (typeof resetAllCodeButtonsOnErrorOrClose === 'function') resetAllCodeButtonsOnErrorOrClose();
            if (typeof setInputDisabledState === 'function') setInputDisabledState(true);
        };

        ws.onclose = (event) => {
            // This is the full onclose logic from your provided script
            console.log("WebSocket connection closed.", event);
            if (typeof addSystemMessage === 'function') addSystemMessage(`Connection closed: ${event.reason || 'Normal closure'} (Code: ${event.code})`);
            if (typeof finalizeTurnOnErrorOrClose === 'function') finalizeTurnOnErrorOrClose();
            if (typeof resetAllCodeButtonsOnErrorOrClose === 'function') resetAllCodeButtonsOnErrorOrClose();
            if (event.code !== 1000 && event.code !== 1005) {
                console.log("Unexpected WebSocket closure. Attempting to reconnect WebSocket in 3 seconds...");
                if (typeof addSystemMessage === 'function') addSystemMessage("Attempting to reconnect...");
                if (typeof setInputDisabledState === 'function') setInputDisabledState(true);
                setTimeout(() => { websocket = null; if (typeof resetStreamingState === 'function') resetStreamingState(); currentAiTurnContainer = null; connectWebSocket(); }, 3000); // Assuming currentAiTurnContainer is global/accessible
            } else { if (typeof setInputDisabledState === 'function') setInputDisabledState(true); }
        };
        // -----------------------------------------------------------------------------------------

        console.log("[DEBUG] WebSocket object created and handlers assigned.");

    } catch (error) {
        console.error("[DEBUG] CRITICAL Error creating WebSocket object:", error);
        if (typeof addErrorMessage === 'function') { addErrorMessage(`WebSocket Creation Error: ${error.message}. Please check server and refresh.`); }
        else { alert(`WebSocket Creation Error: ${error.message}. Please check server and refresh.`); }
        if (typeof setInputDisabledState === 'function') setInputDisabledState(true);
    }
}

// --- Utility Functions --- 
// (debounce function)
// (throttle function defined above)

// Throttled function for highlighting the streaming code block
let throttledStreamHighlight = throttle(() => {
    // Optional: Keep logs to verify throttling
    console.log(">>> throttledStreamHighlight: Attempting highlight..."); 
    console.log(`>>> throttledStreamHighlight: currentProcessingMode = ${currentProcessingMode}`);
    console.log(">>> throttledStreamHighlight: currentCodeBlockElement =", currentCodeBlockElement);

    if (currentCodeBlockElement && currentProcessingMode === MODE_INSIDE_CODE_BLOCK) {
        console.log(">>> throttledStreamHighlight: Conditions met."); 
        try {
            if (typeof Prism !== 'undefined' && typeof Prism.highlightElement === 'function') {
                console.log(">>> throttledStreamHighlight: Calling Prism.highlightElement..."); 
                Prism.highlightElement(currentCodeBlockElement);
                console.log(">>> throttledStreamHighlight: Prism.highlightElement call completed."); 
            } else {
                console.warn(">>> throttledStreamHighlight: Prism.js or highlightElement not available!");
            }
        } catch (e) {
            console.error(">>> throttledStreamHighlight: Error during highlight:", e, currentCodeBlockElement);
        }
    } else {
         console.log(">>> throttledStreamHighlight: Conditions NOT met. Skipping highlight."); 
    }
// Throttle limit in milliseconds (e.g., run at most once every 250ms)
}, 250); // Adjust limit as needed (e.g., 200-500ms) 

// --- End Utility Functions ---

let debouncedStreamHighlight = debounce(() => {
    // --- ADDED LOGGING ---
    console.log(">>> debouncedStreamHighlight: Fired!"); 
    console.log(`>>> debouncedStreamHighlight: currentProcessingMode = ${currentProcessingMode}`);
    console.log(">>> debouncedStreamHighlight: currentCodeBlockElement =", currentCodeBlockElement);
    // --- END OF ADDED LOGGING ---

    // Check if we are currently inside a code block being streamed
    if (currentCodeBlockElement && currentProcessingMode === MODE_INSIDE_CODE_BLOCK) {
        console.log(">>> debouncedStreamHighlight: Conditions met. Attempting highlight."); // Log attempt
        try {
            // Check if Prism and highlightElement are available
            if (typeof Prism !== 'undefined' && typeof Prism.highlightElement === 'function') {
                console.log(">>> debouncedStreamHighlight: Prism.highlightElement found. Calling..."); // Log before call
                Prism.highlightElement(currentCodeBlockElement);
                console.log(">>> debouncedStreamHighlight: Prism.highlightElement call completed."); // Log after call
            } else {
                console.warn(">>> debouncedStreamHighlight: Prism.js or Prism.highlightElement not available!");
            }
        } catch (e) {
            console.error(">>> debouncedStreamHighlight: Error during highlight:", e, currentCodeBlockElement);
        }
    } else {
         console.log(">>> debouncedStreamHighlight: Conditions NOT met. Skipping highlight."); 
    }
}, 300); // Keep your debounce delay (e.g., 300ms)

// Helper function to escape HTML to prevent XSS where markdown is not intended.
function escapeHTML(str) {
    if (str === null || str === undefined) return '';
    return str.toString()
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

/**
 * Renders a single message object (from history) into the chat history UI.
 * Handles user, system, and AI messages, including complex rendering for AI messages
 * (thinking area, code blocks, KaTeX, and Markdown).
 * Strips NO_THINK_PREFIX from historical user messages.
 * @param {object} msg - The message object from the database.
 * @param {HTMLElement} chatHistoryDiv - The main chat history container element.
 * @param {boolean} isHistory - Flag indicating if the message is from loaded history.
 */
function renderSingleMessage(msg, chatHistoryDiv, isHistory = false) {
    // Ensure essential parameters and libraries are available
    if (!chatHistoryDiv || !msg) {
        console.warn("[RenderMessage] Missing chatHistoryDiv or message object. Message:", msg);
        return;
    }
    if (typeof marked === 'undefined') {
        console.error("[RenderMessage] marked.js library is not loaded. Cannot render markdown content.");
        const plainTextDiv = document.createElement('div');
        plainTextDiv.classList.add('message-item', 'p-3', 'rounded-lg', 'max-w-xl', 'mb-2', 'break-words', 'bg-red-100', 'text-red-700');
        plainTextDiv.textContent = `Error: Markdown library not found. Raw content: ${msg.content || 'N/A'}`;
        chatHistoryDiv.appendChild(plainTextDiv);
        return;
    }
    if (typeof katex === 'undefined') {
        console.warn("[RenderMessage] KaTeX library not found. Math expressions may not render correctly.");
    }


    const senderType = msg.sender_type;
    let originalMessageContent = msg.content || ''; // Raw content from DB
    // Use sender_name from DB if available, otherwise determine default based on type
    const senderName = msg.sender_name || (senderType === 'ai' ? 'AI' : (senderType === 'user' ? 'User' : 'System'));
    const timestamp = msg.timestamp;
    const historicalThinkingContent = msg.thinking_content; // For AI messages

    const KATEX_PLACEHOLDER_PREFIX_HISTORICAL = '%%HISTORICAL_KATEX_PLACEHOLDER_';

    // --- Handle User and System Messages (Simpler Rendering) ---
    if (senderType === 'user' || senderType === 'system') {
        const messageDiv = document.createElement('div');
        messageDiv.classList.add('message-item', 'p-3', 'rounded-lg', 'max-w-xl', 'mb-2', 'break-words', 'flex', 'flex-col');
        messageDiv.setAttribute('data-sender', senderType);
        if (msg.id) { messageDiv.setAttribute('data-message-id', String(msg.id)); }
        if (msg.client_id_temp && isHistory) { messageDiv.setAttribute('data-client-id-temp', msg.client_id_temp); }

        if (senderType === 'user') {
            messageDiv.classList.add('bg-emerald-100', 'self-end', 'ml-auto');
        } else { // System message
            messageDiv.classList.add('bg-slate-200', 'self-center', 'mx-auto', 'text-xs', 'italic');
        }

        const senderElem = document.createElement('p');
        senderElem.classList.add('font-semibold', 'text-sm', 'mb-1');
        if (senderType === 'user') senderElem.classList.add('text-emerald-700');
        else senderElem.classList.add('text-slate-600');
        // Use the senderName determined earlier (from DB or default)
        senderElem.textContent = escapeHTML(senderName); // escapeHTML should be defined
        messageDiv.appendChild(senderElem);

        const contentElem = document.createElement('div');
        contentElem.classList.add('text-gray-800', 'text-sm', 'message-content');

        // --- ADDED: Strip NO_THINK_PREFIX for historical user messages ---
        let displayedContent = originalMessageContent;
        if (senderType === 'user' && typeof NO_THINK_PREFIX === 'string' && NO_THINK_PREFIX.length > 0 && displayedContent.startsWith(NO_THINK_PREFIX)) {
            displayedContent = displayedContent.substring(NO_THINK_PREFIX.length);
            // console.log(`[RenderMessage] Stripped NO_THINK_PREFIX from historical user message ID: ${msg.id}`); // Optional log
        }
        // --- END OF ADDED CHANGE ---

        // For user/system, content is parsed as Markdown (after potential prefix stripping for user)
        contentElem.innerHTML = marked.parse(displayedContent);
        messageDiv.appendChild(contentElem);

        if (timestamp) {
            const timestampElem = document.createElement('p');
            timestampElem.classList.add('text-xs', 'text-slate-500', 'mt-1');
            if (senderType === 'user') timestampElem.classList.add('text-right');
            else timestampElem.classList.add('text-center');
            try {
                timestampElem.textContent = new Date(timestamp).toLocaleString();
            } catch (e) {
                timestampElem.textContent = String(timestamp); // Fallback for invalid date
            }
            messageDiv.appendChild(timestampElem);
        }
        chatHistoryDiv.appendChild(messageDiv);

    // --- Handle AI Messages (Complex Rendering - Assumed Correct from Previous Steps) ---
    } else if (senderType === 'ai') {
        // ... (Keep the complex AI message rendering logic from the previous version) ...
        // ... (This includes thinking area, code block extraction/rendering, KaTeX processing) ...
        
        // For brevity, the full AI rendering logic is omitted here, but it should be the
        // same as the one in the previous complete 'renderSingleMessage' function.
        // Ensure the following steps are performed on originalMessageContent:
        // 1. Extract code blocks, render them using createHistoricalCodeBlockDisplay, replace with placeholders.
        // 2. Extract KaTeX, render using katex.renderToString, replace with placeholders.
        // 3. Parse the remaining content with marked.parse().
        // 4. Reinsert the rendered KaTeX HTML.
        // 5. Assemble the full ai-turn-container with thinking, answer bubble, and code blocks area.

        // --- Start of AI Message Rendering Logic (Copied from previous complete version) ---
        const turnIdSuffix = msg.id ? String(msg.id) : `hist-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
        const aiTurnContainer = document.createElement('div');
        aiTurnContainer.classList.add('ai-turn-container');
        aiTurnContainer.setAttribute('data-sender', 'ai');
        if (msg.id) { aiTurnContainer.setAttribute('data-message-id', String(msg.id)); }
        if (msg.client_id_temp && isHistory) { aiTurnContainer.setAttribute('data-client-id-temp', msg.client_id_temp); }
        aiTurnContainer.dataset.turnId = `historical-${turnIdSuffix}`;

        // --- 1. Thinking Area Setup ---
        const thinkingArea = document.createElement('div');
        thinkingArea.classList.add('thinking-area');
        thinkingArea.dataset.turnId = `historical-${turnIdSuffix}`; 
        const details = document.createElement('details');
        details.id = `thinking-details-historical-${turnIdSuffix}`;
        const summary = document.createElement('summary');
        summary.classList.add('thinking-summary');
        const summaryTextSpan = document.createElement('span');
        summaryTextSpan.classList.add('text');
        summaryTextSpan.textContent = 'Show Thinking';
        const summaryDotsSpan = document.createElement('span'); 
        summaryDotsSpan.classList.add('dots');
        summary.appendChild(summaryTextSpan);
        summary.appendChild(summaryDotsSpan);
        const thinkingPreElement = document.createElement('pre');
        if (historicalThinkingContent && historicalThinkingContent.trim() !== "") {
            thinkingPreElement.textContent = historicalThinkingContent;
        } else {
            thinkingPreElement.textContent = '(No historical thinking data available for this AI message)';
            thinkingArea.style.display = 'none'; 
        }
        details.appendChild(summary);
        details.appendChild(thinkingPreElement);
        thinkingArea.appendChild(details);
        aiTurnContainer.appendChild(thinkingArea);
        details.addEventListener('toggle', (event) => {
            const textSpan = event.target.querySelector('.thinking-summary .text');
            if (!textSpan) return;
            textSpan.textContent = event.target.open ? 'Hide Thinking' : 'Show Thinking';
        });

        // --- Main Answer Bubble & Code Blocks Area ---
        const answerElement = document.createElement('div');
        answerElement.classList.add('message', 'ai-message', 'p-3', 'rounded-lg', 'max-w-xl', 'mb-2', 'break-words', 'flex', 'flex-col', 'bg-sky-100', 'self-start', 'mr-auto');
        const codeBlocksArea = document.createElement('div');
        codeBlocksArea.classList.add('code-blocks-area');
        let contentForProcessing = originalMessageContent;

        // --- 2. Extract and Render Code Blocks ---
        let historicalCodeBlockCounter = 0;
        const codeBlockRegex = /```(\w*)\n([\s\S]*?)\n```/g; 
        let matchCode;
        const extractedCodeBlocks = [];
        let tempContentForCodeExtraction = contentForProcessing;
        let contentAfterCodeExtraction = "";
        let lastCodeMatchEndIndex = 0;
        while ((matchCode = codeBlockRegex.exec(tempContentForCodeExtraction)) !== null) {
            historicalCodeBlockCounter++;
            const language = matchCode[1] || 'plaintext'; 
            const code = matchCode[2];
            const placeholder = `%%HISTORICAL_CODE_BLOCK_${historicalCodeBlockCounter}%%`;
            extractedCodeBlocks.push({ language, code, placeholder, index: historicalCodeBlockCounter });
            contentAfterCodeExtraction += tempContentForCodeExtraction.substring(lastCodeMatchEndIndex, matchCode.index);
            contentAfterCodeExtraction += placeholder;
            lastCodeMatchEndIndex = codeBlockRegex.lastIndex; 
        }
        contentAfterCodeExtraction += tempContentForCodeExtraction.substring(lastCodeMatchEndIndex);
        contentForProcessing = contentAfterCodeExtraction; 
        extractedCodeBlocks.forEach(block => {
            if (typeof createHistoricalCodeBlockDisplay === 'function') {
                createHistoricalCodeBlockDisplay(block.language, block.code, turnIdSuffix, block.index, codeBlocksArea);
            } else {
                console.warn('createHistoricalCodeBlockDisplay function is not defined. Cannot render historical code block fully.');
                 const fallbackDiv = document.createElement('div'); 
                 fallbackDiv.textContent = `[Code ${block.index} (${block.language}) - Full display unavailable]`;
                 codeBlocksArea.appendChild(fallbackDiv);
            }
            const referenceText = ` [Code ${block.index}] `;
            contentForProcessing = contentForProcessing.replace(block.placeholder, referenceText);
        });

        // --- 3. Pre-process for KaTeX ---
        const storedHistoricalKatex = {}; 
        let katexPlaceholderIndex = 0;
        const katexRegexGlobal = /(?<!\\)\$\$([\s\S]+?)(?<!\\)\$\$|(?<!\\)\$((?:\\\$|[^$])+?)(?<!\\)\$/g;
        let textForMarkdownParsing = contentForProcessing.replace(katexRegexGlobal, (match, displayContent, inlineContent) => {
            const isDisplayMode = !!displayContent; 
            const katexString = displayContent || inlineContent;
            const cleanedKatexString = katexString.replace(/\\([$])/g, '$1');
            let katexHtml = '';
            if (typeof katex !== 'undefined' && typeof katex.renderToString === 'function') {
                try {
                    katexHtml = katex.renderToString(cleanedKatexString, {
                        displayMode: isDisplayMode, throwOnError: false, output: "html", strict: false 
                    });
                } catch (e) {
                    console.error("Error rendering historical KaTeX:", e, "Original string:", match);
                    katexHtml = `<span class="katex-error" title="${escapeHTML(e.toString())}">${escapeHTML(match)}</span>`;
                }
            } else {
                return match; 
            }
            const placeholderId = `${KATEX_PLACEHOLDER_PREFIX_HISTORICAL}${katexPlaceholderIndex++}`;
            storedHistoricalKatex[placeholderId] = katexHtml; 
            return placeholderId; 
        });

        // --- 4. Markdown Parsing ---
        const senderElemAI = document.createElement('p'); // Use different var name to avoid conflict
        senderElemAI.classList.add('font-semibold', 'text-sm', 'mb-1', 'text-sky-700');
        senderElemAI.textContent = escapeHTML(senderName); // Use senderName which is 'AI' here
        answerElement.appendChild(senderElemAI);
        const contentElemAI = document.createElement('div'); // Use different var name
        contentElemAI.classList.add('text-gray-800', 'text-sm', 'message-content');
        contentElemAI.innerHTML = marked.parse(textForMarkdownParsing); 
        answerElement.appendChild(contentElemAI);

        // --- 5. KaTeX Post-processing ---
        if (Object.keys(storedHistoricalKatex).length > 0) {
            const walker = document.createTreeWalker(contentElemAI, NodeFilter.SHOW_TEXT, null, false);
            let node;
            const textNodesToModify = []; 
            while (node = walker.nextNode()) {
                if (node.nodeValue && node.nodeValue.includes(KATEX_PLACEHOLDER_PREFIX_HISTORICAL)) {
                    textNodesToModify.push(node);
                }
            }
            textNodesToModify.forEach(textNode => {
                let currentTextValue = textNode.nodeValue;
                const parent = textNode.parentNode;
                if (!parent) return;
                const fragment = document.createDocumentFragment(); 
                let lastSplitEnd = 0;
                const placeholderScanRegex = new RegExp(`(${KATEX_PLACEHOLDER_PREFIX_HISTORICAL}\\d+)`, 'g');
                let placeholderMatch;
                while((placeholderMatch = placeholderScanRegex.exec(currentTextValue)) !== null) {
                    const placeholderId = placeholderMatch[1]; 
                    const matchStartIndex = placeholderMatch.index;
                    if (matchStartIndex > lastSplitEnd) {
                        fragment.appendChild(document.createTextNode(currentTextValue.substring(lastSplitEnd, matchStartIndex)));
                    }
                    if (storedHistoricalKatex[placeholderId]) {
                        const katexWrapperSpan = document.createElement('span');
                        katexWrapperSpan.innerHTML = storedHistoricalKatex[placeholderId];
                        if (katexWrapperSpan.childNodes.length === 1 && katexWrapperSpan.firstChild.nodeType === Node.ELEMENT_NODE) {
                             fragment.appendChild(katexWrapperSpan.firstChild);
                        } else {
                             fragment.appendChild(katexWrapperSpan);
                        }
                    } else {
                        fragment.appendChild(document.createTextNode(placeholderId));
                    }
                    lastSplitEnd = placeholderScanRegex.lastIndex; 
                }
                if (lastSplitEnd < currentTextValue.length) {
                    fragment.appendChild(document.createTextNode(currentTextValue.substring(lastSplitEnd)));
                }
                parent.replaceChild(fragment, textNode);
            });
        }

        // --- Timestamp and Final Assembly ---
        if (timestamp) {
            const timestampElemAI = document.createElement('p'); // Use different var name
            timestampElemAI.classList.add('text-xs', 'text-slate-500', 'mt-1', 'text-left');
            try {
                timestampElemAI.textContent = new Date(timestamp).toLocaleString();
            } catch (e) {
                timestampElemAI.textContent = String(timestamp);
            }
            answerElement.appendChild(timestampElemAI);
        }
        aiTurnContainer.appendChild(answerElement); 
        aiTurnContainer.appendChild(codeBlocksArea);  
        chatHistoryDiv.appendChild(aiTurnContainer); 
        // --- End of AI Message Rendering Logic ---
    }
}




/**
 * Fetches chat history for the current session and displays it.
 * @param {string} sessionId - The ID of the current chat session.
 */
async function loadAndDisplayChatHistory(sessionId) {
    const chatHistoryDiv = document.getElementById('chat-history');
    if (!chatHistoryDiv) {
        console.error("Chat history container 'chat-history' not found.");
        return;
    }

    // Update chat session title - assuming it's available via an API or could be fetched.
    // For now, we'll set a generic one or rely on existing logic if it sets the title.
    // const chatSessionTitleElement = document.getElementById('chat-session-title');
    // if (chatSessionTitleElement) chatSessionTitleElement.textContent = `Chat: ${sessionId.substring(0,8)}...`;


    chatHistoryDiv.innerHTML = '<p class="text-center text-gray-500 p-4">Loading history...</p>'; 

    try {
        const response = await fetch(`/api/sessions/${sessionId}/messages`);
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: "Failed to load chat history." }));
            console.error(`Error fetching chat history for session ${sessionId}:`, response.status, errorData.detail);
            chatHistoryDiv.innerHTML = `<p class="text-center text-red-500 p-4">Error loading history: ${escapeHTML(errorData.detail || response.statusText)}</p>`;
            return;
        }

        const messages = await response.json();
        chatHistoryDiv.innerHTML = ''; // Clear loading indicator

        if (messages.length === 0) {
            chatHistoryDiv.innerHTML = '<p class="text-center text-gray-500 p-4">No messages in this session yet. Start chatting!</p>';
        } else {
            messages.forEach(msg => {
                // Use the new renderSingleMessage function
                renderSingleMessage(msg, chatHistoryDiv, true);
            });
            chatHistoryDiv.scrollTop = chatHistoryDiv.scrollHeight; 
        }
        console.log(`Successfully loaded ${messages.length} messages for session ${sessionId}.`);

    } catch (error) {
        console.error(`Failed to fetch or display chat history for session ${sessionId}:`, error);
        chatHistoryDiv.innerHTML = `<p class="text-center text-red-500 p-4">An unexpected error occurred while loading history. Check console.</p>`;
    }
}



/**
 * Creates and appends a display structure for a historical code block,
 * making the code editable, re-highlighting on input, and hiding the
 * run button for unsupported languages.
 * @param {string} language - The language of the code.
 * @param {string} codeContent - The actual code string.
 * @param {string} turnIdSuffix - A unique suffix for element IDs within this historical turn.
 * @param {number} codeBlockIndex - The 1-based index of this code block within the AI turn.
 * @param {HTMLElement} codeBlocksAreaElement - The parent DOM element to append this code block to.
 */
function createHistoricalCodeBlockDisplay(language, codeContent, turnIdSuffix, codeBlockIndex, codeBlocksAreaElement) {
    // Ensure the target area for code blocks exists
    if (!codeBlocksAreaElement) {
        console.error("createHistoricalCodeBlockDisplay: Code blocks area element is null! Cannot append code block.");
        return;
    }

    const blockId = `historical-code-block-turn${turnIdSuffix}-${codeBlockIndex}`;
    const safeLanguage = (language || '').trim().toLowerCase() || 'plaintext';

    // Language aliases and Prism language determination (as before)
    const langAlias = { /* ... keep your existing aliases ... */
        'python': 'python', 'py': 'python', 'javascript': 'javascript', 'js': 'javascript',
        'html': 'markup', 'xml': 'markup', 'svg': 'markup', 'css': 'css', 'bash': 'bash',
        'sh': 'bash', 'shell': 'bash', 'json': 'json', 'yaml': 'yaml', 'yml': 'yaml',
        'markdown': 'markdown', 'md': 'markdown', 'sql': 'sql', 'java': 'java', 'c': 'c',
        'cpp': 'cpp', 'c++': 'cpp', 'csharp': 'csharp', 'cs': 'csharp', 'go': 'go',
        'rust': 'rust', 'php': 'php', 'ruby': 'ruby', 'rb': 'ruby',
        'dockerfile': 'docker', 'docker': 'docker', 'typescript': 'typescript', 'ts': 'typescript',
        'plaintext': 'plain', 'text': 'plain',
     };
    const prismLang = langAlias[safeLanguage] || safeLanguage;
    const displayLang = safeLanguage; // Use the cleaned-up language name for checks/display

    // --- ADD: List of languages supported by the backend Docker execution ---
    // Make sure this list matches the one in createCodeBlockStructure
    const supportedExecutionLanguages = [
        "python", "javascript", "cpp", "csharp", "typescript", "java", "go", "rust"
        // Add or remove languages based on your config.py SUPPORTED_LANGUAGES keys
    ];
    const isLanguageSupported = supportedExecutionLanguages.includes(displayLang);
    // --- END OF ADD ---


    const playIconSvg = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;

    const container = document.createElement('div');
    container.classList.add('code-block-container', 'historical-code-block');
    container.id = blockId;
    container.dataset.language = displayLang;

    const codeHeader = document.createElement('div');
    codeHeader.classList.add('code-block-header');

    const codeButtonsDiv = document.createElement('div');
    codeButtonsDiv.classList.add('code-block-buttons');

    // Create buttons (as before)
    const runStopBtn = document.createElement('button');
    runStopBtn.classList.add('run-code-btn', 'code-action-btn');
    runStopBtn.innerHTML = playIconSvg;
    runStopBtn.title = 'Run Code';
    runStopBtn.dataset.status = 'idle';
    runStopBtn.addEventListener('click', handleRunStopCodeClick);

    const toggleCodeBtn = document.createElement('button');
    toggleCodeBtn.classList.add('toggle-code-btn', 'code-action-btn');
    toggleCodeBtn.textContent = 'Hide';
    toggleCodeBtn.title = 'Show/Hide Code';

    const copyCodeBtn = document.createElement('button');
    copyCodeBtn.classList.add('copy-code-btn', 'code-action-btn');
    copyCodeBtn.textContent = 'Copy';
    copyCodeBtn.title = 'Copy Code';

    // --- ADD: Conditionally hide run button ---
    if (!isLanguageSupported) {
        runStopBtn.style.display = 'none'; // Hide the button entirely
        runStopBtn.title = `Run Code (language '${displayLang}' not supported for execution)`; // Update title
    }
    // --- END OF ADD ---

    // Add buttons to their container
    codeButtonsDiv.appendChild(runStopBtn);
    codeButtonsDiv.appendChild(toggleCodeBtn);
    codeButtonsDiv.appendChild(copyCodeBtn);

    // Code block title (as before)
    const codeTitle = document.createElement('span');
    codeTitle.classList.add('code-block-title');
    codeTitle.textContent = `Code ${codeBlockIndex} (${displayLang})`;
    codeTitle.style.flexGrow = '1';
    codeTitle.style.textAlign = 'left';

    // Assemble header (as before)
    codeHeader.appendChild(codeButtonsDiv);
    codeHeader.appendChild(codeTitle);

    // Create pre/code elements (as before)
    const preElement = document.createElement('pre');
    preElement.classList.add('manual');
    const codeElement = document.createElement('code');
    codeElement.className = `language-${prismLang}`;
    codeElement.setAttribute('contenteditable', 'true'); // Historical code editable
    codeElement.setAttribute('spellcheck', 'false');
    codeElement.textContent = codeContent;

    // Assemble code display area (as before)
    preElement.appendChild(codeElement);
    container.appendChild(codeHeader);
    container.appendChild(preElement);

    // Create output area structure (as before)
    const outputHeader = document.createElement('div');
    outputHeader.classList.add('code-output-header');
    outputHeader.style.display = 'none';
    // ... (rest of output header setup: buttons, title, status span) ...
    const outputButtonsDiv = document.createElement('div');
    outputButtonsDiv.classList.add('code-block-buttons');
    const placeholderSpan = document.createElement('span');
    placeholderSpan.classList.add('output-header-button-placeholder');
    outputButtonsDiv.appendChild(placeholderSpan);
    const toggleOutputBtn = document.createElement('button');
    toggleOutputBtn.classList.add('toggle-output-btn', 'code-action-btn');
    toggleOutputBtn.textContent = 'Hide';
    toggleOutputBtn.title = 'Show/Hide Output';
    const copyOutputBtn = document.createElement('button');
    copyOutputBtn.classList.add('copy-output-btn', 'code-action-btn');
    copyOutputBtn.textContent = 'Copy';
    copyOutputBtn.title = 'Copy Output';
    outputButtonsDiv.appendChild(toggleOutputBtn);
    outputButtonsDiv.appendChild(copyOutputBtn);
    const outputTitle = document.createElement('span');
    outputTitle.classList.add('output-header-title');
    outputTitle.textContent = `Output Code ${codeBlockIndex}`;
    const codeStatusSpan = document.createElement('span');
    codeStatusSpan.classList.add('code-status-span');
    codeStatusSpan.textContent = 'Idle';
    outputHeader.appendChild(outputButtonsDiv);
    outputHeader.appendChild(outputTitle);
    outputHeader.appendChild(codeStatusSpan);

    const outputConsoleDiv = document.createElement('div');
    outputConsoleDiv.classList.add('code-output-console');
    outputConsoleDiv.style.display = 'none';
    const outputPre = document.createElement('pre');
    outputConsoleDiv.appendChild(outputPre);

    // Append output area to container (as before)
    container.appendChild(outputHeader);
    container.appendChild(outputConsoleDiv);

    // Add event listeners (as before)
    // Toggle code listener
    toggleCodeBtn.addEventListener('click', () => {
        const isHidden = preElement.classList.toggle('hidden');
        toggleCodeBtn.textContent = isHidden ? 'Show' : 'Hide';
    });
    // Copy code listener
    copyCodeBtn.addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(codeElement.textContent || '');
            copyCodeBtn.textContent = 'Copied!';
            copyCodeBtn.classList.add('copied');
            setTimeout(() => { copyCodeBtn.textContent = 'Copy'; copyCodeBtn.classList.remove('copied'); }, 1500);
        } catch (err) {
            console.error('Failed to copy historical code: ', err);
            copyCodeBtn.textContent = 'Error';
            setTimeout(() => { copyCodeBtn.textContent = 'Copy'; }, 1500);
        }
    });
    // Run button listener is already attached above
    // Debounced highlight listener for editable historical code (as before)
    const debouncedHistoricalHighlight = debounce(() => {
        const savedPosition = getCursorPosition(codeElement);
        if (savedPosition === -1) { console.warn(`Could not save cursor position for historical block ${blockId}. Highlight may cause cursor jump.`); }
        try {
            const currentText = codeElement.textContent;
            codeElement.innerHTML = ''; 
            codeElement.textContent = currentText; 
            Prism.highlightElement(codeElement); 
            if (savedPosition !== -1) { setCursorPosition(codeElement, savedPosition); }
        } catch (e) {
            console.error(`Error during debounced highlighting for historical block ${blockId}:`, e);
            if (savedPosition !== -1) { setCursorPosition(codeElement, savedPosition); }
        }
    }, 500); 
    codeElement.addEventListener('input', debouncedHistoricalHighlight);
    codeElement.addEventListener('paste', (e) => { setTimeout(debouncedHistoricalHighlight, 100); });
    // Output area listeners (as before)
    toggleOutputBtn.addEventListener('click', () => {
        const isHidden = outputConsoleDiv.classList.toggle('hidden');
        toggleOutputBtn.textContent = isHidden ? 'Show' : 'Hide';
    });
    copyOutputBtn.addEventListener('click', async () => {
        if (!outputPre) return;
        try {
            await navigator.clipboard.writeText(outputPre.textContent || '');
            copyOutputBtn.textContent = 'Copied!';
            copyOutputBtn.classList.add('copied');
            setTimeout(() => { copyOutputBtn.textContent = 'Copy'; copyOutputBtn.classList.remove('copied'); }, 1500);
        } catch (err) {
            console.error('Failed to copy historical output: ', err);
            copyOutputBtn.textContent = 'Error';
            setTimeout(() => { copyOutputBtn.textContent = 'Copy'; }, 1500);
        }
    });

    // Apply initial Prism highlighting (as before)
    if (typeof Prism !== 'undefined' && typeof Prism.highlightElement === 'function') {
        try {
            Prism.highlightElement(codeElement);
        } catch (e) {
            console.error(`Prism highlight error on initial historical code block (lang '${prismLang}', ID: ${blockId}):`, e, codeElement);
        }
    } else {
        console.warn("Prism.js or Prism.highlightElement not available. Historical code will not be highlighted.");
    }

    // Append the fully constructed code block to the designated area
    codeBlocksAreaElement.appendChild(container);
}


// At the top of your script.js (or in a shared scope)
var currentUserInfo = null; // Declare globally, initialize to null

/**
 * Fetches the current authenticated user's details from the /api/me endpoint
 * and populates the global currentUserInfo object. Includes detailed logging.
 */
async function initializeCurrentUser() {
    console.log(">>> initializeCurrentUser: Starting fetch..."); // Log start
    try {
        const response = await fetch('/api/me'); // Call the FastAPI endpoint
        console.log(">>> initializeCurrentUser: Fetch response status:", response.status); // Log status

        if (response.ok) {
            const userData = await response.json();
            console.log(">>> initializeCurrentUser: Received userData from /api/me:", userData); // <-- Log the received data

            // Check if received data has the expected 'name' property
            if (userData && userData.name) {
                window.currentUserInfo = { // Assign to the global variable
                    name: userData.name,
                    email: userData.email,
                    id: userData.id
                };
                console.log(">>> initializeCurrentUser: Assigned to window.currentUserInfo:", window.currentUserInfo); // <-- Log after assignment
            } else {
                console.error(">>> initializeCurrentUser: Received userData is missing 'name' property.", userData);
                window.currentUserInfo = null; // Set to null if data is incomplete
            }
            
        } else if (response.status === 401) { // Unauthorized
            console.warn(">>> initializeCurrentUser: User not authenticated (401).");
            window.currentUserInfo = null; 
        } else {
            console.error(">>> initializeCurrentUser: Failed fetch. Status:", response.status);
            window.currentUserInfo = null;
        }
    } catch (error) {
        console.error(">>> initializeCurrentUser: Error during fetch:", error);
        window.currentUserInfo = null; 
    }
    console.log(">>> initializeCurrentUser: Finished."); // Log end
}

// --- REVISED DOMContentLoaded Event Listener ---
document.addEventListener('DOMContentLoaded', async () => {
    console.log("[DOMContentLoaded] Event fired.");

    // --- 1. Initialize Current User Information FIRST ---
    await initializeCurrentUser(); 
    console.log("[DOMContentLoaded] After initializeCurrentUser awaited. currentUserInfo:", window.currentUserInfo);

    // --- 2. Get DOM Element References ---
    const chatHistory = document.getElementById('chat-history');
    const chatForm = document.getElementById('chat-form');
    const messageInput = document.getElementById('message-input');
    const sendButton = document.getElementById('send-button');
    const thinkCheckbox = document.getElementById('think-checkbox');
    const listElementCheck = document.getElementById('session-list'); 

    // --- 3. Initial UI State & Library Configuration ---
    if (typeof setInputDisabledState === 'function') {
        setInputDisabledState(true); 
    } else {
        console.error("setInputDisabledState function is not defined.");
        if (messageInput) messageInput.disabled = true;
        if (sendButton) sendButton.disabled = true;
        if (thinkCheckbox) thinkCheckbox.disabled = true;
    }

    if (typeof marked !== 'undefined' && typeof marked.setOptions === 'function') {
        marked.setOptions({
            gfm: true, breaks: true, sanitize: false, smartLists: true, smartypants: false,
        });
        console.log("marked.js configured.");
    } else {
        console.warn("[DOMContentLoaded] marked.js or marked.setOptions is not available.");
    }

    // --- 4. Attach Chat Form Submit Event Listener ---
    if (chatForm && messageInput) { 
        console.log("Attaching submit listener to chat form:", chatForm);

        chatForm.addEventListener('submit', (event) => {
            event.preventDefault(); 
            console.log('Chat form submit event intercepted.');
            const userMessage = messageInput.value.trim();
            console.log('User message input:', userMessage);
            if (!userMessage) { 
                console.log('Empty message, returning.');
                return; 
            }
            if (!websocket || websocket.readyState !== WebSocket.OPEN) {
                console.warn("[Submit] WebSocket not ready. State:", websocket?.readyState);
                if (typeof addErrorMessage === 'function') addErrorMessage("Not connected to the server.");
                return;
            }
            try {
                if (typeof addUserMessage === 'function') {
                     console.log('Calling addUserMessage with raw input:', userMessage);
                     addUserMessage(userMessage); 
                } else {
                     console.error('addUserMessage function not found!');
                }
                thinkingRequestedForCurrentTurn = thinkCheckbox ? thinkCheckbox.checked : false;
                console.log('Thinking requested for next turn:', thinkingRequestedForCurrentTurn);
                let messageToSend = userMessage;
                if (!thinkingRequestedForCurrentTurn && typeof NO_THINK_PREFIX === 'string') {
                    messageToSend = NO_THINK_PREFIX + userMessage;
                    console.log('Adding NO_THINK_PREFIX for sending.');
                } 
                if (typeof setupNewAiTurn === 'function') {
                     console.log('Calling setupNewAiTurn.');
                     setupNewAiTurn();
                } else {
                     console.error('setupNewAiTurn function not found!');
                }
                console.log('Sending message via WebSocket:', messageToSend);
                websocket.send(messageToSend);
                messageInput.value = ''; 
                if (typeof setInputDisabledState === 'function') {
                     console.log('Disabling input while AI responds.');
                     setInputDisabledState(true);
                } else {
                     console.error('setInputDisabledState function not found!');
                }
            } catch (sendError) {
                console.error("[Submit] ERROR during message submission process:", sendError);
                if (typeof addErrorMessage === 'function') addErrorMessage(`Failed to send message: ${sendError.message}`);
            }
        });
    } else {
        if (window.location.pathname.includes("/chat/")) {
            console.error("CRITICAL ERROR: Chat form or message input not found.");
            if (typeof addErrorMessage === 'function') addErrorMessage("Initialization Error: Chat input components missing.");
        }
    }

    // --- 5. Session List, History Loading, and WebSocket Connection ---
    if (listElementCheck) {
         console.log("#session-list element found.");
         // if (typeof fetchAndDisplaySessions === 'function') { fetchAndDisplaySessions(); }
    }

    const currentSessionId = (typeof getSessionIdFromPath === 'function') ? getSessionIdFromPath() : null;

    if (currentSessionId) {
        console.log(`[DOMContentLoaded] Initializing for session ID: ${currentSessionId}.`);
        if (typeof loadAndDisplayChatHistory === 'function') {
            console.log(`Calling loadAndDisplayChatHistory for session: ${currentSessionId}`);
            await loadAndDisplayChatHistory(currentSessionId); 
            console.log("loadAndDisplayChatHistory finished.");
        } else {
            console.error('[DOMContentLoaded] loadAndDisplayChatHistory function not defined!');
            if (typeof addErrorMessage === 'function') addErrorMessage("Error: Cannot load chat history.");
        }
        if (typeof connectWebSocket === 'function') {
            console.log("Attempting WebSocket connection.");
            connectWebSocket(); 
        } else {
            console.error('[DOMContentLoaded] connectWebSocket function not defined!');
            if (typeof addErrorMessage === 'function') addErrorMessage("Error: Cannot connect to chat server.");
        }
    } else {
        console.log("[DOMContentLoaded] Not on a specific chat page.");
        if (typeof setInputDisabledState === 'function') {
            setInputDisabledState(true);
        }
    }
    console.log("[DOMContentLoaded] Setup complete.");
}); // End of DOMContentLoaded listener

console.log("[Script] End of script file reached. DOMContentLoaded listener attached.");
