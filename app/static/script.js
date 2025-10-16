// [REPLACE] in app/static/script.js

// --- JS Imports ---
import { marked } from 'marked';
import katex from 'katex';
import Prism from 'prismjs';
import { updateAndDisplayParticipants } from './js/session-manager.js';

import './js/project-explorer.js';

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
import 'prismjs/components/prism-latex'


const katexExtension = {
    name: 'katex',
    level: 'inline',
    start(src) {
        return src.match(/\$|\\\[|\\\(/)?.index;
    },
    tokenizer(src, tokens) {
        const blockRule = /^(?:\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\])/;
        const inlineRule = /^(?:\$((?:\\\$|[^$])+?)\$|\\\(([\s\S]+?)\\\))/;
        let match;

        if (match = blockRule.exec(src)) {
            return {
                type: 'katex',
                raw: match[0],
                text: (match[1] || match[2]).trim(),
                displayMode: true
            };
        }

        if (match = inlineRule.exec(src)) {
            return {
                type: 'katex',
                raw: match[0],
                text: (match[1] || match[2]).trim(),
                displayMode: false
            };
        }
    },
    renderer(token) {
        try {
            return katex.renderToString(token.text, {
                displayMode: token.displayMode,
                throwOnError: false,
                strict: false
            });
        } catch (e) {
            return `<span class="katex-error" title="${escapeHTML(e.toString())}">${escapeHTML(token.raw)}</span>`;
        }
    }
};

marked.use({ extensions: [katexExtension] });

// --- DOM Elements ---
const chatHistory = document.getElementById('chat-history');
const chatForm = document.getElementById('chat-form');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const thinkCheckbox = document.getElementById('think-checkbox');
const stopAiButton = document.getElementById('stop-ai-button');



const LANGUAGE_ALIASES = {
    'python': 'python', 'py': 'python',
    'javascript': 'javascript', 'js': 'javascript',
    'html': 'html',
    'css': 'css',
    'bash': 'bash', 'sh': 'bash', 'shell': 'bash',
    'json': 'json',
    'c': 'c',
    'cpp': 'cpp', 'c++': 'cpp',
    'csharp': 'csharp', 'cs': 'csharp',
    'go': 'go',
    'rust': 'rust',
    'typescript': 'typescript', 'ts': 'typescript',
    'java': 'java',
    'plaintext': 'plaintext', 'text': 'plaintext',
};

const PRISM_LANGUAGE_MAP = {
    'html': 'markup', // Prism's class for HTML is 'markup'
    'xml': 'markup',
    'svg': 'markup',
    'c++': 'cpp',
    'cs': 'csharp',
    'js': 'javascript',
    'py': 'python',
    'ts': 'typescript',
    'sh': 'bash',
    'shell': 'bash'
};


// --- State Variables ---
let websocket;
const clientId = `web-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
let currentTurnId = 0;
let currentCodeBlocksArea = null;
let streamingCodeBlockCounter = 0;
let currentStreamingAnswerElement = null;
let currentStreamingFile = {
    container: null,
    codeElement: null,
    path: null
};
let currentStreamingExplorer = null;
let currentEditingAnswer = { id: null, element: null, buffer: '' };
let currentEditingFile = { path: null, element: null, buffer: '' };
let currentEditingProject = { originalTurnContainer: null };
let currentExtendingFile = { codeElement: null, path: null };

document.addEventListener('fileToggle', (event) => {
    const { path, isChecked, recursive, turnContainer } = event.detail;
    if (!turnContainer) {
        console.error("fileToggle event is missing its turnContainer!");
        return;
    }

    const codeBlocksInScope = turnContainer.querySelectorAll('.block-container[data-path]');
    
    // This part hides/shows the code blocks themselves
    codeBlocksInScope.forEach(block => {
        const blockPath = block.dataset.path;
        let shouldToggle = recursive ? blockPath.startsWith(path) : blockPath === path;
        if (shouldToggle) {
            block.style.display = isChecked ? '' : 'none';
        }
    });

    // This new part specifically links run.sh to its output
    if (path === './run.sh' && !recursive) {
        const runBlock = turnContainer.querySelector('.block-container[data-path="./run.sh"]');
        if (runBlock) {
            const runBlockId = runBlock.id;
            const outputBlockId = `output-for-${runBlockId}`;
            const outputBlock = document.getElementById(outputBlockId);
            if (outputBlock) {
                outputBlock.style.display = isChecked ? '' : 'none';
            }
        }
    }
});

document.addEventListener('downloadClicked', (event) => {
    const { projectId } = event.detail;
    if (projectId) {
        window.location.href = `/api/projects/${projectId}/download`;
    }
});

document.addEventListener('fileUpload', (event) => {
    const { projectId, path, filename, content } = event.detail;
    let responseStatus = 'N/A';

    fetch(`/api/projects/${projectId}/upload`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRF-Token': window.csrfTokenRaw
        },
        body: JSON.stringify({ path, filename, content })
    })
    .then(response => {
        responseStatus = response.status;
        if (!response.ok) {
            return response.json().then(err => {
                const error = new Error(err.detail || 'Upload failed due to an unknown server error.');
                error.response = response;
                throw error;
            });
        }
        return response.json();
    })
    .then(data => {
        console.log("File upload successful, waiting for WebSocket update.", data);
    })
    .catch(error => {
        console.error('File upload error:', error);
        
        let alertMessage = `Error uploading file: ${error.message}\n\n`;
        alertMessage += `Request Status: ${responseStatus}\n`;
        if (error.response) {
            alertMessage += `Endpoint URL: ${error.response.url}`;
        }

        alert(alertMessage);
    });
});

document.addEventListener('fileMove', (event) => {
    const { projectId, sourcePath, destinationPath } = event.detail;
    fetch(`/api/projects/${projectId}/move`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRF-Token': window.csrfTokenRaw
        },
        body: JSON.stringify({ source_path: sourcePath, destination_path: destinationPath })
    })
    .then(response => {
        if (!response.ok) return response.json().then(err => { throw new Error(err.detail || 'Move failed') });
        console.log("File move successful, waiting for WebSocket update.");
    })
    .catch(error => {
        console.error('File move error:', error);
        alert(`Error moving file: ${error.message}`);
    });
});

document.addEventListener('commit-project-changes', (event) => {
    const { projectId, commitMessage, files } = event.detail;
    if (websocket && websocket.readyState === WebSocket.OPEN) {
        console.log("[FRONTEND-TRACE] Sending 'commit_project_changes' message.");
        websocket.send(JSON.stringify({
            type: 'commit_project_changes',
            payload: {
                project_id: projectId,
                commit_message: commitMessage,
                files: files
            }
        }));
    } else {
        alert('Error: WebSocket is not connected. Cannot commit changes.');
    }
});

function escapeHTML(str) {
    if (str === null || str === undefined) return '';
    return str.toString()
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

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
        return preSelectionRange.toString().length;
    } catch (e) {
        console.error("Error getting cursor position:", e);
        return -1;
    }
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
                    const offsetInNode = Math.min(offset - charCount, node.length);
                    range.setStart(node, offsetInNode);
                    foundStart = true;
                } catch (e) {
                    console.error("Error setting range start:", e);
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
    }
}

function initializeCodeBlockHistory(blockId, initialContent) {
    if (!window.codeBlockHistories) {
        window.codeBlockHistories = new Map();
    }
    
    if (!window.codeBlockHistories.has(blockId)) {
        window.codeBlockHistories.set(blockId, {
            history: [initialContent],
            currentIndex: 0,
            saveTimeout: null
        });
    }
}

async function initializeCurrentUser() {
    try {
        const response = await fetch('/api/me');
        if (response.ok) {
            const userData = await response.json();
            if (userData && userData.name) {
                window.currentUserInfo = {
                    name: userData.name,
                    email: userData.email,
                    id: userData.id
                };
            } else {
                window.currentUserInfo = null;
            }
        } else {
            window.currentUserInfo = null;
        }
    } catch (error) {
        window.currentUserInfo = null;
    }
}

function getSessionIdFromPath() {
    const pathName = window.location.pathname;
    const pathParts = pathName.split('/');

    if (pathParts.length >= 3 && pathParts[1] === 'chat') {
        const sessionId = pathParts[2];
        if (sessionId && sessionId.trim() !== "") {
            return sessionId;
        } else {
            console.error("Session ID extracted from path is empty or invalid.");
            return null;
        }
    }

    return null;
}

function scrollToBottom(behavior = 'auto') {
    requestAnimationFrame(() => {
        chatHistory.scrollTo({ top: chatHistory.scrollHeight, behavior: behavior });
    });
}

function setInputDisabledState(inputsDisabled, aiResponding) {
    if (messageInput) messageInput.disabled = inputsDisabled;
    if (sendButton) sendButton.disabled = inputsDisabled;
    if (thinkCheckbox) thinkCheckbox.disabled = inputsDisabled;

    if (stopAiButton) {
        if (aiResponding) {
            stopAiButton.classList.remove('hidden');
            stopAiButton.disabled = false;
            stopAiButton.innerHTML = `
                <svg class="w-5 h-5 inline-block mr-1" fill="currentColor" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8 7a1 1 0 00-1 1v4a1 1 0 102 0V8a1 1 0 00-1-1zm4 0a1 1 0 00-1 1v4a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd"></path></svg>
                Stop`;
            if (sendButton) sendButton.classList.add('hidden');
        } else {
            stopAiButton.classList.add('hidden');
            stopAiButton.disabled = true;
            stopAiButton.innerHTML = `
                <svg class="w-5 h-5 inline-block mr-1" fill="currentColor" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8 7a1 1 0 00-1 1v4a1 1 0 102 0V8a1 1 0 00-1-1zm4 0a1 1 0 00-1 1v4a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd"></path></svg>
                Stop`;
            if (sendButton) sendButton.classList.remove('hidden');
        }
    }
}

function handleAnswerUpdateStart(payload) {
    // For a full update, we just start a new AI turn container.
    // The "move-and-link" logic will be handled when history is reloaded.
    const turnId = currentTurnId;
    const promptingUserId = window.currentUserInfo ? window.currentUserInfo.id : null;
    const promptingUserName = window.currentUserInfo ? window.currentUserInfo.name : "User";
    handleAiThinking({ turn_id: turnId, prompting_user_id: promptingUserId, prompting_user_name: promptingUserName });
}

function handleAnswerEditContent(payload) {
    if (!currentEditingAnswer.element) {
        console.error("No answer selected for editing.");
        return;
    }
    try {
        let currentContent = currentEditingAnswer.element.innerHTML;
        payload.forEach(mod => {
            // Create a RegExp object for global replacement.
            const regex = new RegExp(mod.find, 'g');
            currentContent = currentContent.replace(regex, mod.replace);
        });
        // Rerender the content with Markdown and KaTeX
        renderMarkdownAndKatex(currentContent, currentEditingAnswer.element);
    } catch (e) {
        console.error("Error applying answer edits:", e);
    }
    // Clear the state after applying
    currentEditingAnswer = { id: null, element: null };
}

function handleProjectEditStart(payload) {
    console.log("Starting edit for project ID:", payload.project_to_edit_id);
    const originalContainer = document.querySelector(`.ai-turn-container[data-project-id='${payload.project_to_edit_id}']`);
    if (originalContainer) {
        currentEditingProject.originalTurnContainer = originalContainer;
    } else {
        console.error("Could not find the original project container to edit.");
        currentEditingProject.originalTurnContainer = null;
    }
    
    const turnId = currentTurnId;
    const promptingUserId = window.currentUserInfo ? window.currentUserInfo.id : null;
    const promptingUserName = window.currentUserInfo ? window.currentUserInfo.name : "User";
    handleAiThinking({ turn_id: turnId, prompting_user_id: promptingUserId, prompting_user_name: promptingUserName });
}

function handleProjectUpdateStart(payload) {
    console.log("Starting update for project ID:", payload.project_to_edit_id);
    const { turn_id, project_to_edit_id, prompting_user_id, prompting_user_name } = payload;

    // Use handleAiThinking to create the initial bubble
    handleAiThinking({ turn_id, prompting_user_id, prompting_user_name });
    
    // Now, find that newly created container to add the project elements to it
    const turnContainer = document.querySelector(`.ai-turn-container[data-turn-id='${turn_id}']`);
    if (!turnContainer) {
        console.error("handleProjectUpdateStart: Could not find the turn container for turn_id", turn_id);
        return;
    }

    const originalTurnContainer = document.querySelector(`.ai-turn-container[data-project-id='${project_to_edit_id}']`);
    const originalProjectName = originalTurnContainer ? originalTurnContainer.dataset.projectName : "Updated Project";

    turnContainer.dataset.projectData = JSON.stringify({
        name: originalProjectName,
        files: [],
        prompting_user_id: prompting_user_id
    });

    const explorer = document.createElement('project-explorer');
    explorer.projectData = {
        projectId: null,
        projectName: originalProjectName,
        files: [],
        commits: []
    };
    turnContainer.appendChild(explorer);
    currentStreamingExplorer = explorer;

    const codeBlocksArea = document.createElement('div');
    codeBlocksArea.className = 'code-blocks-area';
    turnContainer.appendChild(codeBlocksArea);
    currentCodeBlocksArea = codeBlocksArea;
}

function handleFileUpdateStart(payload) {
    // This reuses the logic for starting a new file stream, creating a code block container for the new content.
    handleStartFileStream(payload);
}

function handleFileExtendStart(payload) {
    // This also reuses the file stream logic. The frontend just needs to display the appended content.
    handleStartFileStream(payload);
}

function handleFileEditContent(payload) {
    if (!currentEditingFile.element) {
        console.error("No file selected for editing.");
        return;
    }
    try {
        let currentContent = currentEditingFile.element.textContent;
        payload.forEach(mod => {
            const regex = new RegExp(mod.find, 'g');
            currentContent = currentContent.replace(regex, mod.replace);
        });
        currentEditingFile.element.textContent = currentContent;
        Prism.highlightElement(currentEditingFile.element);
    } catch (e) {
        console.error("Error applying file edits:", e);
    }
    // Clear the state after applying
    currentEditingFile = { path: null, element: null };
}

function handleTypingIndicators(payload) {
    if (window.currentUserInfo && payload.user_id === window.currentUserInfo.id) {
        return;
    }
    const { user_id, user_name, is_typing, color } = payload;
    const indicatorId = `typing-indicator-${user_id}`;
    let existingIndicator = document.getElementById(indicatorId);

    if (is_typing && !existingIndicator) {
        const bubble = document.createElement('div');
        bubble.id = indicatorId;
        bubble.classList.add('message-item', 'p-3', 'rounded-lg', 'max-w-xl', 'mb-2', 'flex', 'flex-col', 'self-start', 'mr-auto');
        bubble.style.backgroundColor = color || '#dbeafe';

        const senderElem = document.createElement('p');
        senderElem.classList.add('font-semibold', 'text-sm', 'mb-1', 'text-gray-800');
        senderElem.textContent = escapeHTML(user_name);

        const dots = document.createElement('span');
        dots.classList.add('loading-dots');

        bubble.appendChild(senderElem);
        bubble.appendChild(dots);
        chatHistory.appendChild(bubble);
        scrollToBottom('smooth');
    } else if (!is_typing && existingIndicator) {
        existingIndicator.remove();
    }
}

function updateParticipantListUI(active_user_id = null) {
    const participants = document.querySelectorAll('#participant-list li[id^="participant-"]');
    participants.forEach(p => {
        const nameSpan = p.querySelector('.participant-name-span');
        if (nameSpan) {
            const oldIndicator = nameSpan.querySelector('.ai-indicator');
            if(oldIndicator) oldIndicator.remove();

            if (p.id === `participant-${active_user_id}`) {
                const indicator = document.createElement('span');
                indicator.className = 'ai-indicator text-xs text-gray-500 font-semibold ml-1';
                indicator.textContent = '(+AI)';
                nameSpan.appendChild(indicator);
            }
        }
    });
}

function processStreamChunk(chunk) {
    let text = contentBuffer + chunk;
    contentBuffer = ""; // Clear the buffer now that we're processing it

    while (text.length > 0) {
        if (!isParsingBlock) {
            const match = text.match(/_([A-Z_]+)_START_/);
            if (match) {
                // We found the start of a new command block.
                const plainTextBefore = text.substring(0, match.index);
                if (plainTextBefore) {
                    handleAnswerChunk(plainTextBefore); // Render any conversational text
                }

                isParsingBlock = true;
                currentBlockType = match[1];
                jsonBuffer = ""; // Reset JSON buffer for the new block
                text = text.substring(match.index + match[0].length);
            } else {
                // No more commands in this chunk, render the rest as plain text.
                handleAnswerChunk(text);
                return;
            }
        }

        if (isParsingBlock) {
            // We are inside a block, looking for the end of its JSON part.
            const jsonEndMatch = text.match(/_JSON_END_/);
            if (jsonEndMatch) {
                jsonBuffer += text.substring(0, jsonEndMatch.index);
                text = text.substring(jsonEndMatch.index + jsonEndMatch[0].length);

                try {
                    const jsonData = JSON.parse(jsonBuffer);
                    if (currentBlockType === 'FILE' || currentBlockType === 'UPDATE_FILE') {
                        handleStartFileStream({ turn_id: currentTurnId, ...jsonData });
                    }
                } catch (e) {
                    console.error("Parser JSON Error:", e, "Buffer:", jsonBuffer);
                }
                
                // The JSON is done. Now we are in the content part of the block.
                const endTag = `_${currentBlockType}_END_`;
                const endTagIndex = text.indexOf(endTag);

                if (endTagIndex !== -1) {
                    // The end tag is in this same chunk.
                    const content = text.substring(0, endTagIndex);
                    
                    if (currentBlockType === 'FILE' || currentBlockType === 'UPDATE_FILE') {
                        if (content) handleFileChunk({ content: content });
                        handleEndFileStream({ turn_id: currentTurnId });
                    } else { // For UPDATE_PROJECT, etc.
                        if (content) handleAnswerChunk(content);
                    }
                    
                    text = text.substring(endTagIndex + endTag.length);
                    resetParser(); // This block is fully complete. Reset for the next one.
                } else {
                    // The end tag is NOT in this chunk. The rest of the chunk is content.
                    // IMPORTANT: We must re-scan this content for nested blocks.
                    isParsingBlock = false; // Temporarily exit block mode to find nested blocks
                    contentBuffer = text;   // Buffer the content to re-process
                    text = ""; // Exit the while loop for this chunk
                }

            } else {
                // The _JSON_END_ tag is not in this chunk. Buffer for JSON and wait for the next chunk.
                jsonBuffer += text;
                return;
            }
        }
    }
}

function handleEndAnswerStream(payload) {
    if (currentStreamingAnswerElement && currentStreamingAnswerElement.dataset.rawContent) {
        renderMarkdownAndKatex(currentStreamingAnswerElement.dataset.rawContent, currentStreamingAnswerElement);
    }
    currentStreamingAnswerElement = null;
    updateParticipantListUI(null);
}

// [REPLACE] in app/static/script.js

function handleEndFileStream(payload) {
    if (currentStreamingFile && currentStreamingFile.codeElement) {
        Prism.highlightElement(currentStreamingFile.codeElement);

        const container = currentStreamingFile.container;
        const codeElement = currentStreamingFile.codeElement;
        if (container && codeElement) {
            // --- START FIX: Set the baseline content for future edits ---
            const finalContent = codeElement.textContent || '';
            container.dataset.originalContent = finalContent;

            // Also update the in-memory project data on the turn container so "Run" works correctly.
            const turnContainer = container.closest('.ai-turn-container');
            if (turnContainer && turnContainer.dataset.projectData) {
                try {
                    const projectData = JSON.parse(turnContainer.dataset.projectData);
                    const fileInProject = projectData.files.find(f => f.path === currentStreamingFile.path);
                    if (fileInProject) {
                        fileInProject.content = finalContent;
                        turnContainer.dataset.projectData = JSON.stringify(projectData);
                    }
                } catch (e) {
                    console.error("Error updating project data after file stream:", e);
                }
            }
            // --- END FIX ---
        }
    }
    currentStreamingFile = { container: null, codeElement: null, path: null, fileData: null };
}

function finalizeTurnOnErrorOrClose() {
    console.log("[STREAM] Finalizing turn.");
    setInputDisabledState(false, false);
    if (messageInput) messageInput.focus();

    currentStreamingAnswerElement = null;
    currentStreamingFile = {
        container: null,
        codeElement: null,
        path: null
    };
    updateParticipantListUI(null);
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
    if (currentAiTurnContainer) {
        const target = currentAnswerElement || currentCodeBlocksArea || currentAiTurnContainer;
        target.appendChild(messageElement);
    } else {
        chatHistory.appendChild(messageElement);
    }
    setTimeout(() => scrollToBottom('smooth'), 50);
}

function renderMarkdownAndKatex(contentString, targetElement) {
    if (typeof contentString !== 'string' || !targetElement) {
        if (targetElement) targetElement.innerHTML = "";
        return;
    }

    // With the extension, marked now handles KaTeX automatically.
    // The placeholder logic is no longer needed.
    const html = marked.parse(contentString);
    targetElement.innerHTML = html;

    // We still need to highlight code blocks after rendering.
    targetElement.querySelectorAll('pre code[class*="language-"]').forEach((block) => {
        if (typeof Prism !== 'undefined') {
            Prism.highlightElement(block);
        }
    });
}

function createCodeBlock(language, codeContent, originalCodeForDataset, turnIdSuffix, codeBlockIndex, codeBlocksAreaElement, isRunnable = false, promptingUserId = null) {
    if (!codeBlocksAreaElement) {
        console.error("createCodeBlock: Code blocks area element is null!");
        return;
    }
    const rawLang = (language || 'plaintext').trim().toLowerCase();
    const canonicalLang = LANGUAGE_ALIASES[rawLang] || 'plaintext';
    const prismLang = PRISM_LANGUAGE_MAP[canonicalLang] || canonicalLang;

    const container = document.createElement('div');
    container.classList.add('block-container');
    container.dataset.language = canonicalLang;
    container.dataset.originalContent = originalCodeForDataset;
    container.dataset.edited = 'false';

    const codeHeader = document.createElement('div');
    codeHeader.classList.add('block-header');

    if (promptingUserId !== null && window.participantInfo) {
        const prompterColor = window.participantInfo[promptingUserId]?.color || '#dbeafe';
        const aiColor = window.participantInfo['AI']?.color || '#E0F2FE';
        codeHeader.style.background = `linear-gradient(to right, ${aiColor}, ${prompterColor})`;
    }

    const codeButtonsDiv = document.createElement('div');
    codeButtonsDiv.classList.add('block-buttons');
    const runStopBtn = document.createElement('button');
    runStopBtn.classList.add('run-code-btn', 'block-action-btn');
    runStopBtn.dataset.status = 'idle';
    runStopBtn.innerHTML = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;
    runStopBtn.title = 'Run Project';
    runStopBtn.addEventListener('click', handleRunStopCodeClick);
    if (!isRunnable) {
        runStopBtn.style.display = 'none';
    }
    codeButtonsDiv.appendChild(runStopBtn);
    
    const restoreBtn = document.createElement('button');
    restoreBtn.classList.add('restore-code-btn', 'block-action-btn');
    restoreBtn.textContent = 'Restore';
    restoreBtn.title = 'Restore Original Code';
    const copyCodeBtn = document.createElement('button');
    copyCodeBtn.classList.add('copy-code-btn', 'block-action-btn');
    copyCodeBtn.textContent = 'Copy';
    copyCodeBtn.title = 'Copy Code';

    codeButtonsDiv.appendChild(restoreBtn);
    codeButtonsDiv.appendChild(copyCodeBtn);
    
    const codeTitle = document.createElement('span');
    codeTitle.classList.add('block-title');
    const titleTextSpan = document.createElement('span');
    titleTextSpan.classList.add('title-text');
    titleTextSpan.textContent = `Code Block`;
    codeTitle.appendChild(titleTextSpan);
    
    codeHeader.appendChild(codeButtonsDiv);
    codeHeader.appendChild(codeTitle);

    const preElement = document.createElement('pre');
    preElement.classList.add('manual');
    const codeElement = document.createElement('code');
    codeElement.className = `language-${prismLang}`;
    codeElement.setAttribute('contenteditable', 'true');
    codeElement.setAttribute('spellcheck', 'false');
    codeElement.textContent = codeContent;

    preElement.appendChild(codeElement);
    container.appendChild(codeHeader);
    container.appendChild(preElement);
    codeBlocksAreaElement.appendChild(container);

    if (typeof Prism !== 'undefined' && typeof Prism.highlightElement === 'function') {
        try { Prism.highlightElement(codeElement); } catch (e) { console.error(`Prism highlight error:`, e); }
    }

    copyCodeBtn.addEventListener('click', async () => { try { await navigator.clipboard.writeText(codeElement.textContent || ''); copyCodeBtn.textContent = 'Copied!'; setTimeout(() => { copyCodeBtn.textContent = 'Copy'; }, 1500); } catch (err) { console.error('Failed to copy code: ', err); } });
    
    restoreBtn.addEventListener('click', async () => { 
        const originalContent = container.dataset.originalContent; 
        codeElement.textContent = originalContent;
        if(typeof Prism !== 'undefined') Prism.highlightElement(codeElement);
        
        container.dataset.edited = 'false';
        updateProjectCommitState(container);
        
        const sessionId = getSessionIdFromPath();
        if (sessionId) {
            fetch(`/api/sessions/${sessionId}/edited-blocks/${container.id}`, {
                method: 'DELETE',
                headers: { 'X-CSRF-Token': window.csrfTokenRaw }
            });
        }
    });
    
    codeElement.addEventListener('input', () => {
        const cursorPosition = getCursorPosition(codeElement);
        const currentText = codeElement.textContent || '';
        
        if (typeof Prism !== 'undefined') {
            Prism.highlightElement(codeElement);
        }
        
        setCursorPosition(codeElement, cursorPosition);
        
        const isEdited = currentText !== container.dataset.originalContent;
        container.dataset.edited = isEdited.toString();
        updateProjectCommitState(container);

        clearTimeout(codeElement._saveTimeout);
        codeElement._saveTimeout = setTimeout(() => {
            const blockId = container.id;
            console.log(`[FRONTEND-TRACE] Debounced save triggered for block ${blockId}`);
            saveCodeBlockContent(blockId, currentText);
        }, 2000);
    });

    // --- THE FIX: Attach the keydown listener to handle Ctrl+Z and Ctrl+Y ---
    codeElement.addEventListener('keydown', (e) => {
        handleCodeBlockKeydown(e, container.id);
    });

    return container;
}

function updateProjectCommitState(container) {
    const turnContainer = container.closest('.ai-turn-container');
    if (!turnContainer) return;

    const explorer = turnContainer.querySelector('project-explorer');
    if (!explorer) return;

    const allBlocks = turnContainer.querySelectorAll('.block-container[data-path]');
    const hasAnyEdits = Array.from(allBlocks).some(block => block.dataset.edited === 'true');

    if (hasAnyEdits) {
        if (typeof explorer.showUncommittedState === 'function') {
            explorer.showUncommittedState();
        }
    } else {
        if (typeof explorer.hideUncommittedState === 'function') {
            explorer.hideUncommittedState();
        }
    }
}

function handleCodeWaitingInput(payload) {
    const { project_id } = payload;
    const outputBlockId = `output-for-${project_id}`;
    const outputContainer = document.getElementById(outputBlockId);
    if (!outputContainer) return;

    const textarea = outputContainer.querySelector('.console-textarea');
    if (!textarea) return;

    // 1. Store the content length BEFORE the user can type.
    const textBeforeInput = textarea.value.length;

    // 2. Unlock the textarea and focus it.
    textarea.readOnly = false;
    textarea.focus();

    // 3. CRITICAL: Move the cursor to the very end of the text.
    textarea.selectionStart = textarea.selectionEnd = textarea.value.length;

    const handleEnter = (e) => {
        // We only care about the "Enter" key, and not when "Shift" is also pressed.
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault(); // Stop "Enter" from adding a newline itself.

            const currentText = textarea.value;
            // 4. Extract ONLY the text the user just typed.
            const userInput = currentText.substring(textBeforeInput);

            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send(JSON.stringify({
                    type: 'code_input',
                    payload: { project_id: project_id, input: userInput.trimEnd() } // Send the clean input
                }));
            }

            // 5. Add the final newline and lock the textarea again.
            textarea.value += '\n';
            textarea.readOnly = true;

            // 6. Clean up the listener to prevent it from firing again.
            textarea.removeEventListener('keydown', handleEnter);
        }
    };

    textarea.addEventListener('keydown', handleEnter);
}

function renderPlotWhenReady(plotDivId, plotData, timeout = 3000) {
    const startTime = Date.now();
    const interval = setInterval(() => {
        if (typeof mpld3 !== 'undefined' && typeof mpld3.draw_figure === 'function') {
            clearInterval(interval);
            
            try {
                // Let mpld3 handle the initial drawing.
                mpld3.draw_figure(plotDivId, plotData);

                // --- NEW LOGIC START: Make the generated SVG responsive ---
                const plotDiv = document.getElementById(plotDivId);
                if (plotDiv) {
                    const svg = plotDiv.querySelector('svg');
                    if (svg) {
                        const originalWidth = svg.getAttribute('width');
                        const originalHeight = svg.getAttribute('height');

                        if (originalWidth && originalHeight) {
                            // Set the viewBox to the original dimensions
                            svg.setAttribute('viewBox', `0 0 ${originalWidth} ${originalHeight}`);
                            // Ensure aspect ratio is maintained
                            svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
                            
                            // Remove fixed dimensions and use CSS for responsive scaling
                            svg.removeAttribute('width');
                            svg.removeAttribute('height');
                            svg.style.width = '100%';
                            svg.style.height = 'auto';
                        }
                    }
                }
                // --- NEW LOGIC END ---

            } catch (error) {
                console.error('Error during mpld3.draw_figure:', error);
                const plotDiv = document.getElementById(plotDivId);
                if (plotDiv) {
                    plotDiv.textContent = "Error rendering plot. See browser console for details.";
                }
            }

        } else if (Date.now() - startTime > timeout) {
            clearInterval(interval);
            const plotDiv = document.getElementById(plotDivId);
            if (plotDiv) {
                plotDiv.textContent = "Error: Timed out waiting for mpld3.js library to load.";
            }
            console.error("Timed out waiting for mpld3 to become available.");
        }
    }, 50);
}

function handleStructuredMessage(messageData) {
    const { type, payload } = messageData;
    console.log(`[FRONTEND-TRACE] Received WebSocket message. Type: ${type}`, payload);

    if (!payload || !payload.project_id) return;

    const projectId = payload.project_id;
    const codeContainer = document.getElementById(projectId);
    const outputBlockId = `output-for-${projectId}`;
    let outputContainer = document.getElementById(outputBlockId);

    if (!outputContainer && type !== 'code_output') {
        return;
    }
    
    switch (type) {
        case 'code_output': {
            if (!outputContainer) {
                const codeBlock = document.getElementById(projectId);
                outputContainer = createOrClearOutputContainer(projectId, codeBlock);
            }
            // --- THE FIX IS HERE ---
            // Find the new '.console-textarea' instead of the old 'pre' tag.
            const textarea = outputContainer.querySelector('.console-textarea');
            if (textarea) {
                // Call the updated addCodeOutput function.
                addCodeOutput(textarea, payload.data);
                outputContainer.style.display = '';
            }
            // --- END FIX ---
            break;
        }
        case 'code_waiting_input': {
            handleCodeWaitingInput(payload);
            break;
        }
        case 'code_finished': {
            const { exit_code, error, full_output, persistent_project_id } = payload;
            console.log(`[FRONTEND-TRACE] Handling 'code_finished'. Exit: ${exit_code}, Error: ${error}, Output Length: ${full_output ? full_output.length : 0}`);
            let finishMessage = `Finished (Exit: ${exit_code})`;
            let statusClass = (exit_code === 0) ? 'success' : 'error';

            if (error) {
                finishMessage = (error === "Stopped by user.") ? "Stopped" : "Failed";
                statusClass = 'error';
                if (outputContainer) {
                    const textarea = outputContainer.querySelector('.console-textarea');
                    if (textarea) addCodeOutput(textarea, `\n[Program finished with error: ${error}]`);
                }
            } else if (full_output && persistent_project_id && websocket && websocket.readyState === WebSocket.OPEN) {
                console.log(`[FRONTEND-TRACE] Sending 'save_code_run' for project ${persistent_project_id}`);
                websocket.send(JSON.stringify({
                    type: 'save_code_run',
                    payload: {
                        project_id: persistent_project_id,
                        output: full_output,
                    }
                }));
            }
            
            if (outputContainer) {
                const isPreview = outputContainer.querySelector('iframe');
                if (isPreview) {
                    outputContainer.style.display = 'none';
                }
                updateHeaderStatus(outputContainer, finishMessage, statusClass);
            }

            if (codeContainer) {
                const runStopBtn = codeContainer.querySelector('.run-code-btn');
                if (runStopBtn) {
                    runStopBtn.dataset.status = 'idle';
                    runStopBtn.innerHTML = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;
                    runStopBtn.title = 'Run Code';
                }
            }
            break;
        }
    }
}

function createOrClearOutputContainer(projectId, codeContainer, title, promptingUserId) {
    const outputBlockId = `output-for-${projectId}`;
    let outputContainer = document.getElementById(outputBlockId);

    if (outputContainer) {
        const textarea = outputContainer.querySelector('.console-textarea');
        if (textarea) {
            textarea.value = '';
            textarea.readOnly = true;
        }
    } else {
        outputContainer = document.createElement('div');
        outputContainer.id = outputBlockId;
        outputContainer.className = 'block-container';

        const outputHeader = document.createElement('div');
        outputHeader.className = 'block-header';
        outputHeader.innerHTML = createOutputHeaderHTML(title, promptingUserId);
        const prompterColor = window.participantInfo?.[promptingUserId]?.color || '#dbeafe';
        const aiColor = window.participantInfo?.['AI']?.color || '#E0F2FE';
        outputHeader.style.background = `linear-gradient(to right, ${aiColor}, ${prompterColor})`;

        const outputConsoleDiv = document.createElement('div');
        outputConsoleDiv.className = 'block-output-console';

        const textarea = document.createElement('textarea');
        textarea.className = 'console-textarea';
        textarea.spellcheck = false;
        textarea.readOnly = true; // Start in a locked, read-only state

        outputConsoleDiv.appendChild(textarea);
        outputContainer.appendChild(outputHeader);
        outputContainer.appendChild(outputConsoleDiv);

        outputHeader.querySelector('.toggle-output-btn').addEventListener('click', (e) => {
            const isHidden = outputConsoleDiv.classList.toggle('hidden');
            e.target.textContent = isHidden ? 'Show' : 'Hide';
        });
        outputHeader.querySelector('.copy-output-btn').addEventListener('click', async (e) => {
            await navigator.clipboard.writeText(textarea.value || '');
            e.target.textContent = 'Copied!';
            setTimeout(() => { e.target.textContent = 'Copy'; }, 1500);
        });

        if (codeContainer) {
            codeContainer.insertAdjacentElement('afterend', outputContainer);
        } else {
            chatHistory.appendChild(outputContainer);
        }
    }
    outputContainer.style.display = '';
    return outputContainer;
}

function createPlotBlock(projectId, plotData, plotIndex) {
    const codeContainer = document.getElementById(projectId);
    if (!codeContainer) return;

    const uniquePlotBlockId = `${projectId}-plot-${plotIndex}`;
    const existingPlot = document.getElementById(uniquePlotBlockId);
    if (existingPlot) existingPlot.remove();

    const outputContainer = createOrClearOutputContainer(projectId);
    
    const plotContainer = document.createElement('div');
    plotContainer.id = uniquePlotBlockId;
    plotContainer.className = 'block-container';

    const plotHeader = document.createElement('div');
    plotHeader.className = 'block-header';
    plotHeader.innerHTML = `
        <div class="block-buttons">
            <button class="toggle-output-btn block-action-btn">Hide</button>
        </div>
        <span class="block-title">Plot Output ${plotIndex}</span>
        <span class="block-status success">Rendered</span>
    `;

    const plotDivId = `plot-div-${uniquePlotBlockId}`;
    const plotDiv = document.createElement('div');
    plotDiv.id = plotDivId;
    plotDiv.className = 'mpld3-plot-container';

    plotContainer.appendChild(plotHeader);
    plotContainer.appendChild(plotDiv);
    outputContainer.insertAdjacentElement('afterend', plotContainer);

    plotHeader.querySelector('.toggle-output-btn').addEventListener('click', (e) => {
        plotContainer.classList.toggle('hidden');
        e.target.textContent = plotContainer.classList.contains('hidden') ? 'Show' : 'Hide';
    });

    renderPlotWhenReady(plotDivId, plotData);
}

function saveCodeBlockContent(blockId, content) {
    const container = document.getElementById(blockId);
    if (!container) return;
    
    const language = container.dataset.language;
    const sessionId = getSessionIdFromPath();
    
    if (!sessionId || !websocket || websocket.readyState !== WebSocket.OPEN) {
        return;
    }
    
    websocket.send(JSON.stringify({
        type: 'save_code_content',
        payload: {
            session_id: sessionId,
            code_block_id: blockId,
            language: language,
            code_content: content
        }
    }));
}

function createOutputHeaderHTML(title, promptingUserId, statusText = 'Running...', statusClass = 'running') {
    const prompterColor = window.participantInfo?.[promptingUserId]?.color || '#dbeafe';
    const aiColor = window.participantInfo?.['AI']?.color || '#E0F2FE';
    const gradient = `linear-gradient(to right, ${aiColor}, ${prompterColor})`;
    return `
        <div class="block-buttons">
            <button class="toggle-output-btn block-action-btn">Hide</button>
            <button class="copy-output-btn block-action-btn">Copy</button>
        </div>
        <span class="block-title" style="color: #374151;">Output of ${escapeHTML(title)}</span>
        <span class="block-status ${statusClass}">${statusText}</span>
    `;
}

async function handleRunStopCodeClick(event) {
    const button = event.currentTarget;
    const container = button.closest('.block-container');
    if (!container) return;

    const runBlockId = container.id;
    const status = button.dataset.status;

    console.log(`[FRONTEND-TRACE] Run/Stop button clicked. Status: ${status}, Block ID: ${runBlockId}`);

    if (status === 'running' || status === 'previewing') {
        const outputBlockId = `output-for-${runBlockId}`;
        const outputContainer = document.getElementById(outputBlockId);
        if (outputContainer) {
            updateHeaderStatus(outputContainer, 'Stopping...', 'stopping');
        }
        if (websocket && websocket.readyState === WebSocket.OPEN) {
            console.log("[FRONTEND-TRACE] Sending 'stop_code' message.");
            websocket.send(JSON.stringify({ type: 'stop_code', payload: { project_id: runBlockId } }));
        }
        return;
    }

    const turnContainer = button.closest('.ai-turn-container');
    const explorer = turnContainer ? turnContainer.querySelector('project-explorer') : null;
    if (!explorer || !explorer.projectData) {
        alert("Error: Could not find project data for this execution.");
        return;
    }
    
    const persistentProjectId = explorer.projectData.projectId || explorer.projectData.id;
    const projectData = explorer.projectData;
    
    const updatedProjectData = JSON.parse(JSON.stringify(projectData));
    const allCodeBlocksInTurn = turnContainer.querySelectorAll('.block-container[id^="code-block-turn"]');

    allCodeBlocksInTurn.forEach(block => {
        const path = block.dataset.path;
        const codeElem = block.querySelector('code');
        if (path && codeElem) {
            const fileInProject = updatedProjectData.files.find(f => f.path === path);
            if (fileInProject) {
                fileInProject.content = codeElem.textContent;
            }
        }
    });

    let mainLanguage = 'bash';
    const languagePriority = ['html', 'csharp', 'java', 'go', 'rust', 'python', 'cpp', 'c', 'typescript', 'javascript'];
    const projectLanguages = new Set(updatedProjectData.files.map(file => file.language || ''));
    for (const lang of languagePriority) {
        if (projectLanguages.has(lang)) {
            mainLanguage = lang;
            break;
        }
    }

    if (websocket && websocket.readyState === WebSocket.OPEN) {
        const langConfigResponse = await fetch('/static/languages.json');
        const langConfigs = await langConfigResponse.json();
        const isPreviewServer = langConfigs[mainLanguage] && langConfigs[mainLanguage].is_preview_server;

        button.dataset.status = isPreviewServer ? 'previewing' : 'running';
        button.innerHTML = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><rect width="100" height="100" rx="15"/></svg>`;
        button.title = 'Stop Execution';

        if (!isPreviewServer) {
            const titleElement = container.querySelector('.block-title .title-text');
            const title = titleElement ? titleElement.textContent : 'run.sh';
            const promptingUserId = JSON.parse(turnContainer.dataset.projectData).prompting_user_id || null;
            const outputContainer = createOrClearOutputContainer(runBlockId, container, title, promptingUserId);
            updateHeaderStatus(outputContainer, 'Running...', 'running');
        }

        const payload = {
            project_data: updatedProjectData,
            project_id: runBlockId,
            persistent_project_id: persistentProjectId,
            language: mainLanguage
        };
        console.log("[FRONTEND-TRACE] Sending 'run_code' message with payload:", payload);
        websocket.send(JSON.stringify({ type: 'run_code', payload }));
    } else {
        addErrorMessage("Cannot run code: Not connected to server.");
    }
}

function updateHeaderStatus(outputContainer, statusText, statusClass) {
    const statusSpan = outputContainer.querySelector('.block-status');
    if (statusSpan) {
        statusSpan.textContent = statusText;
        statusSpan.className = `block-status ${statusClass}`;
    }
    
    const outputHeader = outputContainer.querySelector('.block-header');
    if (outputHeader && outputHeader.classList.contains('header-is-sticky-js')) {
        const stickyId = outputHeader.dataset.stickyId;
        if (stickyId) {
            const stickyClone = document.getElementById(stickyId);
            if (stickyClone) {
                const stickyStatusSpan = stickyClone.querySelector('.block-status');
                if (stickyStatusSpan) {
                    stickyStatusSpan.textContent = statusText;
                    stickyStatusSpan.className = `block-status ${statusClass}`;
                }
            }
        }
    }
}

function addCodeOutput(textarea, text) {
    if (!textarea || !text) return;

    textarea.value += text;
    // Auto-scroll to the bottom as new output arrives
    textarea.scrollTop = textarea.scrollHeight;
}

function performUndo(blockId) {
    if (!window.codeBlockHistories) return false;
    
    const historyData = window.codeBlockHistories.get(blockId);
    if (!historyData || historyData.currentIndex <= 0) return false;
    
    historyData.currentIndex--;
    const content = historyData.history[historyData.currentIndex];
    
    const container = document.getElementById(blockId);
    if (!container) return false;
    
    const codeElement = container.querySelector('code');
    if (!codeElement) return false;
    
    const cursorPos = getCursorPosition(codeElement);
    codeElement.textContent = content;
    
    if (typeof Prism !== 'undefined' && typeof Prism.highlightElement === 'function') {
        try {
            Prism.highlightElement(codeElement);
            setCursorPosition(codeElement, Math.min(cursorPos, content.length));
        } catch (e) {
            console.error(`Prism highlight error:`, e);
        }
    }
    
    return true;
}

function performRedo(blockId) {
    if (!window.codeBlockHistories) return false;
    
    const historyData = window.codeBlockHistories.get(blockId);
    if (!historyData || historyData.currentIndex >= historyData.history.length - 1) return false;
    
    historyData.currentIndex++;
    const content = historyData.history[historyData.currentIndex];
    
    const container = document.getElementById(blockId);
    if (!container) return false;
    
    const codeElement = container.querySelector('code');
    if (!codeElement) return false;
    
    const cursorPos = getCursorPosition(codeElement);
    codeElement.textContent = content;
    
    if (typeof Prism !== 'undefined' && typeof Prism.highlightElement === 'function') {
        try {
            Prism.highlightElement(codeElement);
            setCursorPosition(codeElement, Math.min(cursorPos, content.length));
        } catch (e) {
            console.error(`Prism highlight error:`, e);
        }
    }
    
    return true;
}

function handleCodeBlockKeydown(event, blockId) {
    const isCtrlZ = event.ctrlKey && event.key === 'z' && !event.shiftKey;
    const isCtrlY = event.ctrlKey && (event.key === 'y' || (event.key === 'z' && event.shiftKey));
    
    if (isCtrlZ) {
        event.preventDefault();
        performUndo(blockId);
        return;
    }
    
    if (isCtrlY) {
        event.preventDefault();
        performRedo(blockId);
        return;
    }
    
    const codeElement = event.target;
    const content = codeElement.textContent || '';
    saveCodeBlockState(blockId, content);
}

function saveCodeBlockState(blockId, content) {
    if (!window.codeBlockHistories) return;
    
    const historyData = window.codeBlockHistories.get(blockId);
    if (!historyData) return;

    clearTimeout(historyData.saveTimeout);
    historyData.saveTimeout = setTimeout(() => {
        const lastContent = historyData.history[historyData.currentIndex];
        if (lastContent !== content) {
            historyData.history = historyData.history.slice(0, historyData.currentIndex + 1);
            historyData.history.push(content);
            
            if (historyData.history.length > 50) {
                historyData.history.shift();
            } else {
                historyData.currentIndex++;
            }
        }
    }, 2000);
}


async function renderSingleMessage(msg, parentElement, isHistory = false, editedCodeBlocks = {}) {
    if (!parentElement || !msg) return;

    let specialContent = null;
    if (typeof msg.content === 'string' && msg.content.trim().startsWith('{')) {
        try {
            const parsed = JSON.parse(msg.content);
            if (parsed && (parsed.type === 'deleted' || parsed.type === 'link')) {
                specialContent = parsed;
            }
        } catch (e) {}
    }

    if (specialContent && specialContent.type === 'deleted') {
        const placeholder = document.createElement('div');
        placeholder.className = 'deleted-message-placeholder';
        placeholder.dataset.messageId = msg.id;
        const wasMyMessage = window.currentUserInfo && msg.user_id === window.currentUserInfo.id;
        if (wasMyMessage) {
            placeholder.classList.add('self-end', 'ml-auto');
        } else {
            placeholder.classList.add('self-start', 'mr-auto');
        }
        const text = document.createElement('span');
        text.textContent = `(message deleted by ${specialContent.deleted_by || 'a user'})`;
        const dismissBtn = document.createElement('span');
        dismissBtn.className = 'dismiss-deleted-btn';
        dismissBtn.innerHTML = '&times;';
        dismissBtn.title = 'Dismiss';
        dismissBtn.onclick = async () => {
            placeholder.remove();
            try {
                await fetch(`/api/messages/${msg.id}/hide`, {
                    method: 'DELETE',
                    headers: { 'X-CSRF-Token': window.csrfTokenRaw }
                });
            } catch (error) {
                console.error('Failed to save hidden state:', error);
            }
        };
        placeholder.appendChild(text);
        placeholder.appendChild(dismissBtn);
        parentElement.appendChild(placeholder);
        return;
    }

    if (specialContent && specialContent.type === 'link') {
        const turnContainer = document.createElement('div');
        turnContainer.className = 'ai-turn-container';
        turnContainer.dataset.messageId = msg.id;
        const linkElem = document.createElement('div');
        linkElem.className = 'system-message italic text-blue-600 cursor-pointer hover:underline';
        linkElem.textContent = specialContent.text || 'Content has been updated. Click to view.';
        linkElem.addEventListener('click', () => {
            const targetElement = document.querySelector(`.ai-turn-container[data-message-id='${specialContent.target_message_id}']`);
            if (targetElement) targetElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
        turnContainer.appendChild(linkElem);
        parentElement.appendChild(turnContainer);
        return;
    }

    if (msg.sender_type === 'user') {
        const messageBubble = document.createElement('div');
        messageBubble.className = 'message-item p-3 rounded-lg max-w-xl mb-2 break-words flex flex-col';
        messageBubble.dataset.messageId = msg.id;
        if (msg.turn_id) messageBubble.dataset.turnId = msg.turn_id;
        const isCurrentUser = window.currentUserInfo && msg.user_id === window.currentUserInfo.id;
        messageBubble.classList.add(isCurrentUser ? 'self-end' : 'self-start', isCurrentUser ? 'ml-auto' : 'mr-auto');
        let bubbleColor = msg.sender_color || (window.participantInfo && msg.user_id ? window.participantInfo[msg.user_id]?.color : '#e5e7eb');
        messageBubble.style.backgroundColor = bubbleColor;
        const senderElem = document.createElement('p');
        senderElem.className = 'font-semibold text-sm mb-1 text-gray-800';
        senderElem.textContent = msg.sender_name;
        const contentElem = document.createElement('div');
        contentElem.className = 'text-gray-800 text-sm message-content';
        contentElem.innerHTML = marked.parse((msg.content || '').replace(/@\w+/g, '').trim());
        const deleteBtn = createDeleteButton(msg.id, msg.user_id);
        if (deleteBtn) {
            messageBubble.appendChild(deleteBtn);
        }
        messageBubble.appendChild(senderElem);
        messageBubble.appendChild(contentElem);
        parentElement.appendChild(messageBubble);
        return;
    }
    
    const turnContainer = document.createElement('div');
    turnContainer.className = 'ai-turn-container';
    if (msg.turn_id) turnContainer.dataset.turnId = msg.turn_id;
    turnContainer.dataset.messageId = msg.id;
    const hasProject = msg.project_files && msg.project_files.length > 0;
    let contentToRender = msg.content || "";

    if (msg.sender_type === 'ai') {
        const isProjectBlock = /_PROJECT_START_/.test(contentToRender) || /_UPDATE_PROJECT_START_/.test(contentToRender);
        const isEditBlock = /_EDIT_/.test(contentToRender);
        const isAnswerBlock = /_ANSWER_START_/.test(contentToRender) || /_UPDATE_ANSWER_START_/.test(contentToRender);

        if (hasProject && isProjectBlock) {
            const introMatch = contentToRender.match(/_JSON_END_([\s\S]*?)(?=_FILE_START_|_PROJECT_END_)/);
            contentToRender = introMatch ? introMatch[1].trim() : "";
        } else if (isAnswerBlock) {
            const answerMatch = contentToRender.match(/_JSON_END_([\s\S]*?)_ANSWER_END_/);
            contentToRender = answerMatch ? answerMatch[1].trim() : "";
        } else if (isEditBlock) {
            contentToRender = "";
        }
    }
    
    const hasContent = contentToRender.trim().length > 0;

    if (hasContent) {
        const messageBubble = document.createElement('div');
        const contentElem = document.createElement('div');
        const senderElem = document.createElement('p');
        contentElem.className = 'text-gray-800 text-sm message-content';
        senderElem.className = 'font-semibold text-sm mb-1 text-gray-800';
        messageBubble.className = 'message ai-message';
        const prompterId = msg.prompting_user_id;
        const prompterInfo = prompterId && window.participantInfo ? window.participantInfo[prompterId] : null;
        const prompterColor = prompterInfo?.color || '#dbeafe';
        const aiColor = window.participantInfo?.['AI']?.color || '#E0F2FE';
        messageBubble.style.background = `linear-gradient(to right, ${aiColor}, ${prompterColor})`;
        senderElem.textContent = prompterInfo ? `AI Assistant - Prompted by ${prompterInfo.name}` : 'AI Assistant';
        senderElem.classList.add('italic');
        renderMarkdownAndKatex(contentToRender, contentElem);
        const deleteBtn = createDeleteButton(msg.id, msg.user_id, turnContainer);
        if (deleteBtn) {
            messageBubble.appendChild(deleteBtn);
        }
        messageBubble.appendChild(senderElem);
        messageBubble.appendChild(contentElem);
        turnContainer.appendChild(messageBubble);
    }

    if (hasProject) {
        turnContainer.dataset.projectId = msg.project_id;
        turnContainer.dataset.projectName = msg.project_name;
        turnContainer.dataset.projectData = JSON.stringify({
            name: msg.project_name || "Code Project",
            files: msg.project_files,
            prompting_user_id: msg.prompting_user_id
        });
        const explorer = document.createElement('project-explorer');
        explorer.projectData = {
            projectId: msg.project_id,
            projectName: msg.project_name || 'Code Project',
            files: msg.project_files,
            commits: msg.project_commits || []
        };
        turnContainer.appendChild(explorer);
        const codeBlocksArea = document.createElement('div');
        codeBlocksArea.className = 'code-blocks-area';

        // Simplified logic: The explorer now handles uncommitted state dynamically.
        // We just render the blocks based on the data we have.
        const outputLogFile = msg.project_files.find(f => f.path.endsWith('_run_output.log'));
        const outputContent = outputLogFile ? outputLogFile.content : null;
        let runBlockElement = null;

        msg.project_files.forEach((file, index) => {
            if (file.path.endsWith('_run_output.log')) return;
            const codeBlockIndex = index + 1;
            const isRunnable = file.path.endsWith('run.sh');
            const safeBtoa = btoa(file.path).replace(/[/+=]/g, '');
            const stableId = `code-block-turn${msg.turn_id}-${safeBtoa}`;
            
            // On history load, check for edits and apply them.
            const finalCodeContent = (isHistory && editedCodeBlocks[stableId]) ? editedCodeBlocks[stableId] : file.content;
            
            const block = createCodeBlock(
                file.language, finalCodeContent, file.content,
                msg.turn_id, codeBlockIndex, codeBlocksArea,
                isRunnable, msg.prompting_user_id
            );
            if (block) {
                block.id = stableId;
                block.dataset.path = file.path;
                block.querySelector('.block-title .title-text').textContent = file.path;
                initializeCodeBlockHistory(block.id, finalCodeContent);
                if (isRunnable) {
                    runBlockElement = block;
                }
            }
        });
        turnContainer.appendChild(codeBlocksArea);

        if (runBlockElement && outputContent !== null) {
            const outputContainer = createOrClearOutputContainer(runBlockElement.id, runBlockElement, "run.sh", msg.prompting_user_id);
            const textarea = outputContainer.querySelector('.console-textarea');
            if (textarea) {
                textarea.value = outputContent;
            }
            updateHeaderStatus(outputContainer, 'Finished (from history)', 'success');
        }
    }

    if (turnContainer.hasChildNodes()) {
        parentElement.appendChild(turnContainer);
    }
}

function handleProjectPreviewReady(payload) {
    const { project_id, url } = payload;
    const runBlockContainer = document.getElementById(project_id);
    if (!runBlockContainer) return;

    const outputBlockId = `output-for-${project_id}`;
    let previewContainer = document.getElementById(outputBlockId);
    
    if (previewContainer) {
        previewContainer.innerHTML = ''; 
    } else {
        previewContainer = document.createElement('div');
        previewContainer.id = outputBlockId;
        previewContainer.className = 'block-container';
        runBlockContainer.insertAdjacentElement('afterend', previewContainer);
    }
    
    previewContainer.style.display = 'block';

    const turnContainer = runBlockContainer.closest('.ai-turn-container');
    const promptingUserId = turnContainer ? (JSON.parse(turnContainer.dataset.projectData).prompting_user_id || null) : null;
    
    const prompterColor = window.participantInfo?.[promptingUserId]?.color || '#dbeafe';
    const aiColor = window.participantInfo?.['AI']?.color || '#E0F2FE';
    const gradient = `linear-gradient(to right, ${aiColor}, ${prompterColor})`;

    previewContainer.innerHTML = `
        <div class="block-header" style="background: ${gradient};">
            <div class="header-left block-buttons">
                <span class="block-title">Preview</span>
                <button id="refresh-${project_id}" class="block-action-btn">Refresh</button>
                <button id="hide-${project_id}" class="block-action-btn">Hide</button>
            </div>
            <div class="header-right">
                <span class="block-status success">Preview Running</span>
            </div>
        </div>
        <iframe id="iframe-${project_id}" src="${url}" style="width: 100%; height: 500px; border: none; border-top: 1px solid #d1d5db; background-color: white;"></iframe>
    `;

    const iframe = previewContainer.querySelector(`#iframe-${project_id}`);
    const refreshBtn = previewContainer.querySelector(`#refresh-${project_id}`);
    const hideBtn = previewContainer.querySelector(`#hide-${project_id}`);

    refreshBtn.addEventListener('click', () => {
        iframe.contentWindow.location.reload();
    });
    
    hideBtn.addEventListener('click', () => {
        const isHidden = previewContainer.style.display === 'none';
        previewContainer.style.display = isHidden ? 'block' : 'none';
        hideBtn.textContent = isHidden ? 'Show' : 'Hide';
    });
}

function handleProjectHeader(payload) {
    const { turn_id, name, prompting_user_id } = payload;
    const turnContainer = document.querySelector(`.ai-turn-container[data-turn-id='${turn_id}']`);
    if (!turnContainer) return;

    turnContainer.dataset.projectData = JSON.stringify({
        name: name,
        files: [],
        prompting_user_id: prompting_user_id
    });

    const explorer = document.createElement('project-explorer');
    explorer.projectData = {
        projectId: null,
        projectName: name,
        files: [],
        commits: []
    };
    turnContainer.appendChild(explorer);
    currentStreamingExplorer = explorer;

    const codeBlocksArea = document.createElement('div');
    codeBlocksArea.className = 'code-blocks-area';
    turnContainer.appendChild(codeBlocksArea);
    currentCodeBlocksArea = codeBlocksArea;
}

function stickHeader(header, scrollerRect) {
    if (!header || header.classList.contains('header-is-sticky-js')) return;
    
    const container = header.parentElement;
    const containerRect = container.getBoundingClientRect();
    const stickyId = `sticky-${header.classList[0]}-${Date.now()}`;
    
    if (document.getElementById(stickyId)) return;
    
    const stickyClone = header.cloneNode(true);
    stickyClone.id = stickyId;
    stickyClone.style.position = 'fixed';
    stickyClone.style.top = `${scrollerRect.top}px`;
    stickyClone.style.left = `${containerRect.left}px`;
    stickyClone.style.width = `${containerRect.width}px`;
    stickyClone.style.zIndex = '1000';
    stickyClone.style.backgroundColor = '#e5e7eb';
    stickyClone.style.borderRadius = '0';
    stickyClone.style.boxShadow = 'none';
    stickyClone.style.overflow = 'hidden';
    
    const originalButtons = header.querySelectorAll('button');
    const cloneButtons = stickyClone.querySelectorAll('button');
    
    cloneButtons.forEach((cloneBtn, index) => {
        const originalBtn = originalButtons[index];
        if (originalBtn) {
            cloneBtn.disabled = originalBtn.disabled;
            cloneBtn.className = originalBtn.className;
            cloneBtn.innerHTML = originalBtn.innerHTML;
            cloneBtn.dataset.status = originalBtn.dataset.status;
            
            cloneBtn.onclick = function(e) {
                e.preventDefault();
                e.stopPropagation();
                originalBtn.click();
            };
            
            const syncButton = () => {
                cloneBtn.disabled = originalBtn.disabled;
                cloneBtn.className = originalBtn.className;
                cloneBtn.innerHTML = originalBtn.innerHTML;
                cloneBtn.dataset.status = originalBtn.dataset.status;
            };
            
            const observer = new MutationObserver(syncButton);
            observer.observe(originalBtn, { 
                attributes: true, 
                attributeFilter: ['class', 'disabled', 'data-status'],
                childList: true,
                subtree: true
            });
            
            cloneBtn._syncObserver = observer;
        }
    });
    
    document.body.appendChild(stickyClone);
    header.classList.add('header-is-sticky-js');
    header.dataset.stickyId = stickyId;
}

function unstickHeader(header) {
    if (!header || !header.classList.contains('header-is-sticky-js')) return;

    const stickyId = header.dataset.stickyId;
    if (stickyId) {
        const stickyClone = document.getElementById(stickyId);
        if (stickyClone) {
            // Clean up button observers
            const cloneButtons = stickyClone.querySelectorAll('button');
            cloneButtons.forEach(btn => {
                if (btn._syncObserver) {
                    btn._syncObserver.disconnect();
                    delete btn._syncObserver;
                }
            });
            
            // NEW: Clean up status observer
            const cloneStatusSpan = stickyClone.querySelector('.block-status');
            if (cloneStatusSpan && cloneStatusSpan._syncObserver) {
                cloneStatusSpan._syncObserver.disconnect();
                delete cloneStatusSpan._syncObserver;
            }
            
            stickyClone.remove();
        }
    }

    header.classList.remove('header-is-sticky-js');
    delete header.dataset.stickyId;
}

function handleAiThinking(payload) {
    const { turn_id, prompting_user_id, prompting_user_name } = payload;
    let turnContainer = document.querySelector(`.ai-turn-container[data-turn-id='${turn_id}']`);
    if (turnContainer) return; // Already exists

    turnContainer = document.createElement('div');
    turnContainer.className = 'ai-turn-container';
    turnContainer.dataset.turnId = turn_id;

    const messageBubble = document.createElement('div');
    messageBubble.className = 'message ai-message';

    // Look up prompter info safely, with a fallback to the name from the payload
    const prompterInfo = window.participantInfo ? window.participantInfo[prompting_user_id] : null;
    const finalPrompterName = prompterInfo ? prompterInfo.name : prompting_user_name;
    const prompterColor = prompterInfo ? prompterInfo.color : '#dbeafe';
    const aiColor = window.participantInfo && window.participantInfo['AI'] ? window.participantInfo['AI'].color : '#E0F2FE';

    messageBubble.style.background = `linear-gradient(to right, ${aiColor}, ${prompterColor})`;

    const senderElem = document.createElement('p');
    senderElem.className = 'font-semibold text-sm mb-1 text-gray-800 italic';
    senderElem.textContent = `AI Assistant - Prompted by ${finalPrompterName}`;

    const contentArea = document.createElement('div');
    contentArea.className = 'text-gray-800 text-sm message-content live-ai-content-area';
    contentArea.innerHTML = '<span class="loading-dots"></span>';

    messageBubble.appendChild(senderElem);
    messageBubble.appendChild(contentArea);
    turnContainer.appendChild(messageBubble);
    chatHistory.appendChild(turnContainer);

    currentStreamingAnswerElement = contentArea;
    scrollToBottom('smooth');
}

async function handleAiStreamEnd(payload) {
    console.log("AI stream ended. Final payload received:", payload);
    finalizeTurnOnErrorOrClose();

    if (currentStreamingExplorer && payload && payload.project_id) {
        const projectId = payload.project_id;
        try {
            const response = await fetch(`/api/projects/${projectId}`);
            if (response.ok) {
                const finalProjectData = await response.json();
                finalProjectData.projectId = finalProjectData.id;
                
                // Pass the final data to the explorer and let it handle the UI synchronization.
                currentStreamingExplorer.updateData(finalProjectData);
            } else {
                console.error("Error fetching final project data. Status:", response.status);
            }
        } catch (error) {
            console.error("A network or script error occurred while fetching final project data:", error);
        }
    }
    
    currentStreamingExplorer = null;
    currentCodeBlocksArea = null;
}

function handleEndProjectEdit(payload) {
    console.log("Project edit block finished.");
    currentEditingProject.originalTurnContainer = null;
}

function handleEndFileEdit(payload) {
    if (currentEditingFile.element && currentEditingFile.buffer) {
        try {
            const edits = JSON.parse(currentEditingFile.buffer);
            handleFileEditContent(edits); // Reuse existing logic
        } catch (e) {
            console.error("Failed to parse edit JSON for file:", currentEditingFile.path, e);
            currentEditingFile.element.textContent += "\n\n--- PARSE ERROR: FAILED TO APPLY EDITS ---";
        }
    }
    currentEditingFile = { path: null, element: null, buffer: '' };
}

function handleEndAnswerEdit(payload) {
    if (currentEditingAnswer.element && currentEditingAnswer.buffer) {
        try {
            const edits = JSON.parse(currentEditingAnswer.buffer);
            handleAnswerEditContent(edits); // Reuse existing logic
        } catch (e) {
            console.error("Failed to parse edit JSON for answer:", currentEditingAnswer.id, e);
        }
    }
    currentEditingAnswer = { id: null, element: null, buffer: '' };
}

function handleEndFileExtend(payload) {
    // This is essentially the same as ending a regular file stream.
    handleEndFileStream(payload);
}

function handleAnswerEditStart(payload) {
    const originalMessage = document.querySelector(`[data-message-id='${payload.answer_to_edit_id}'] .message-content`);
    if (originalMessage) {
        currentEditingAnswer = { id: payload.answer_to_edit_id, element: originalMessage, buffer: '' };
        console.log("Preparing to edit answer ID:", payload.answer_to_edit_id);
    }
}

function handleFileEditStart(payload) {
    if (!currentEditingProject.originalTurnContainer) {
        console.error("Cannot start file edit: no original project container is being tracked.");
        return;
    }
    const fileBlock = currentEditingProject.originalTurnContainer.querySelector(`.block-container[data-path='${payload.path}']`);
    if (fileBlock) {
        currentEditingFile = { path: payload.path, element: fileBlock.querySelector('code'), buffer: '' };
        console.log("Found file to edit:", payload.path);
    } else {
        console.error("Could not find file block with path:", payload.path);
    }
}

function handleFileChunk(payload) {
    if (currentEditingFile.element) {
        currentEditingFile.buffer += payload.content;
        return;
    }

    if (!currentStreamingFile || !currentStreamingFile.codeElement) return;

    const codeElement = currentStreamingFile.codeElement;

    if (typeof codeElement.dataset.rawContent === 'undefined') {
        codeElement.dataset.rawContent = '';
    }
    codeElement.dataset.rawContent += payload.content;

    if (typeof Prism !== 'undefined') {
        const language = PRISM_LANGUAGE_MAP[currentStreamingFile.language] || currentStreamingFile.language;
        const grammar = Prism.languages[language];
        if (grammar) {
            const highlightedHtml = Prism.highlight(codeElement.dataset.rawContent, grammar, language);
            codeElement.innerHTML = highlightedHtml;
        } else {
            codeElement.textContent = codeElement.dataset.rawContent;
        }
    }
    
    // Update file size in the explorer UI
    if (currentStreamingExplorer && currentStreamingFile.path) {
        currentStreamingFile.size += (new TextEncoder().encode(payload.content)).length;
        currentStreamingExplorer.updateFileSize(currentStreamingFile.path, currentStreamingFile.size);
    }

    requestAnimationFrame(() => {
        if (currentStreamingFile && currentStreamingFile.container) {
             currentStreamingFile.container.scrollIntoView({ behavior: 'auto', block: 'nearest' });
        }
    });
}

function handleAnswerChunk(chunk) {
    if (currentEditingAnswer.element) {
        currentEditingAnswer.buffer += chunk;
        return; 
    }

    if (!currentStreamingAnswerElement) {
        console.warn("handleAnswerChunk called, but no streaming element exists.");
        return;
    }
    
    const loadingDots = currentStreamingAnswerElement.querySelector('.loading-dots');
    if (loadingDots) {
        currentStreamingAnswerElement.innerHTML = '';
    }

    const currentContent = (currentStreamingAnswerElement.dataset.rawContent || '') + chunk;
    currentStreamingAnswerElement.dataset.rawContent = currentContent;
    renderMarkdownAndKatex(currentContent, currentStreamingAnswerElement);

    requestAnimationFrame(() => {
        if (currentStreamingAnswerElement) {
            currentStreamingAnswerElement.scrollIntoView({ behavior: 'auto', block: 'end' });
        }
    });
}

function handleStartFileStream(payload) {
    if (!currentCodeBlocksArea) {
        console.error("Cannot start file stream: a project block was not properly initiated.");
        return;
    }

    if (currentStreamingExplorer) {
        currentStreamingExplorer.addFile({
            path: payload.path,
            content: '',
            language: payload.language,
            size: 0,
            lastModified: Date.now()
        });
    }

    streamingCodeBlockCounter++;
    const isRunnable = payload.path.endsWith('run.sh');
    const promptingUserId = payload.prompting_user_id;
    const turnIdSuffix = payload.turn_id;

    const newBlock = createCodeBlock(
        payload.language || 'plaintext', '', '', turnIdSuffix,
        streamingCodeBlockCounter, currentCodeBlocksArea, isRunnable,
        promptingUserId
    );

    // --- THE FIX IS HERE ---
    // We were missing this logic to assign a stable ID to the new block.
    if (newBlock) {
        const safeBtoa = btoa(payload.path).replace(/[/+=]/g, '');
        const stableId = `code-block-turn${turnIdSuffix}-${safeBtoa}`;
        newBlock.id = stableId; // This is the critical missing line.

        newBlock.dataset.path = payload.path;
        newBlock.querySelector('.block-title .title-text').textContent = payload.path;

        // Also initialize its history for undo/redo functionality
        initializeCodeBlockHistory(newBlock.id, '');
    }
    // --- END FIX ---

    currentStreamingFile = {
        container: newBlock,
        codeElement: newBlock.querySelector('code'),
        path: payload.path,
        size: 0,
        language: payload.language || 'plaintext'
    };
}

function connectWebSocket() {
    let sessionId = getSessionIdFromPath();
    if (!sessionId) {
        addErrorMessage("Cannot connect to chat: Invalid session ID in URL.");
        setInputDisabledState(true, false);
        return;
    }
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/${sessionId}/${clientId}`;
    try {
        const ws = new WebSocket(wsUrl);
        websocket = ws;
        ws.onopen = () => { console.log("[WS_CLIENT] WebSocket connection opened."); setInputDisabledState(false, false); addSystemMessage("Connected to the server."); };
        ws.onmessage = (event) => {
            if (typeof event.data !== 'string') return;
            const messageData = JSON.parse(event.data);
            
            console.log("[WebSocket Client] Received message:", messageData);

            if (event.data.startsWith("<ERROR>")) { addErrorMessage(event.data.substring(7)); finalizeTurnOnErrorOrClose(); return; }
            
            switch (messageData.type) {
                case 'ai_thinking': handleAiThinking(messageData.payload); break;
                case 'project_header': handleProjectHeader(messageData.payload); break;
                case 'ai_chunk': handleAnswerChunk(messageData.payload); break;
                case 'end_answer_stream': handleEndAnswerStream(messageData.payload); break;
                case 'start_file_stream': handleStartFileStream(messageData.payload); break;
                case 'file_chunk': handleFileChunk(messageData.payload); break;
                case 'end_file_stream': handleEndFileStream(messageData.payload); break;
                case 'answer_edit_start': handleAnswerEditStart(messageData.payload); break;
                case 'answer_update_start': handleAnswerUpdateStart(messageData.payload); break;
                // 'answer_edit_content' is handled by buffering in handleAnswerChunk
                case 'end_answer_edit': handleEndAnswerEdit(messageData.payload); break;
                case 'end_answer_update': handleEndAnswerStream(messageData.payload); break;
                case 'project_edit_start': handleProjectEditStart(messageData.payload); break;
                case 'project_update_start': handleProjectUpdateStart(messageData.payload); break;
                case 'end_project_edit': handleEndProjectEdit(messageData.payload); break;
                case 'end_project_update': console.log("Project update block finished."); break;
                case 'file_update_start': handleFileUpdateStart(messageData.payload); break;
                case 'file_extend_start': handleFileExtendStart(messageData.payload); break;
                case 'file_edit_start': handleFileEditStart(messageData.payload); break;
                case 'end_file_edit': handleEndFileEdit(messageData.payload); break;
                case 'end_file_update': handleEndFileStream(messageData.payload); break;
                case 'end_file_extend': handleEndFileExtend(messageData.payload); break;
                case 'ai_stream_end': handleAiStreamEnd(messageData.payload); break;
                case 'project_updated':
                    if (messageData.payload && messageData.payload.project_id) {
                        const { project_id, project_data } = messageData.payload;
                        const explorer = document.querySelector(`.ai-turn-container[data-project-id="${project_id}"] project-explorer`);
                        if (explorer) explorer.updateData(project_data);
                    }
                    break;
                case 'project_preview_ready': handleProjectPreviewReady(messageData.payload); break;
                case 'participant_typing': handleTypingIndicators(messageData.payload); break;
                case 'participants_update': updateAndDisplayParticipants(messageData.payload); break;
                case 'session_deleted': 
                    const chatTitle = document.getElementById('chat-session-title'); 
                    if (chatTitle) chatTitle.textContent = `[Deleted] ${chatTitle.textContent}`; 
                    const banner = document.createElement('div'); 
                    banner.className = 'text-center text-sm text-red-700 bg-red-100 p-2 rounded-md font-semibold'; 
                    banner.textContent = 'The host has deleted this session. The chat is now read-only.'; 
                    chatHistory.prepend(banner); 
                    setInputDisabledState(true, false); 
                    break;
                case 'new_message': {
                    const message = messageData.payload;
                    if (!message) break;

                    const isMyMessage = window.currentUserInfo && message.user_id === window.currentUserInfo.id;

                    if (isMyMessage) {
                        console.log(`[WS RECV MSG | new_message] This is my message returning. Turn ID: ${message.turn_id}`);
                        const tempMsgElement = document.querySelector(`.message-item[data-turn-id='${message.turn_id}']`);
                        
                        if (tempMsgElement) {
                            console.log(`[WS RECV MSG | new_message] Found temporary element to replace:`, tempMsgElement);
                            const tempContainer = document.createElement('div');
                            renderSingleMessage(message, tempContainer, false, {});
                            const finalMsgElement = tempContainer.firstChild;

                            if (finalMsgElement) {
                                console.log(`[WS RECV MSG | new_message] Replacing temporary element with final element:`, finalMsgElement);
                                tempMsgElement.replaceWith(finalMsgElement);
                            } else {
                                console.warn(`[WS RECV MSG | new_message] Failed to create final element, removing temporary one.`);
                                tempMsgElement.remove();
                            }
                            
                            scrollToBottom('smooth');
                            break;
                        } else {
                            console.warn(`[WS RECV MSG | new_message] Could not find temporary element for turn ID ${message.turn_id} to replace.`);
                        }
                    }
                    
                    console.log(`[WS RECV MSG | new_message] Rendering message from another user.`);
                    renderSingleMessage(message, chatHistory, true, {});
                    scrollToBottom('smooth');
                    break;
                }

                case 'message_updated': {
                    const { message_id, new_content } = messageData.payload;
                    const elementToUpdate = document.querySelector(`[data-message-id='${message_id}']`);

                    if (elementToUpdate) {
                        try {
                            const parsedContent = JSON.parse(new_content);
                            // Check if this update is specifically for a deletion.
                            if (parsedContent && parsedContent.type === 'deleted') {
                                const placeholder = document.createElement('div');
                                placeholder.className = 'deleted-message-placeholder';
                                placeholder.dataset.messageId = message_id;

                                // Keep the placeholder on the same side as the original message.
                                if (elementToUpdate.classList.contains('self-end')) {
                                    placeholder.classList.add('self-end', 'ml-auto');
                                } else {
                                    placeholder.classList.add('self-start', 'mr-auto');
                                }

                                const text = document.createElement('span');
                                text.textContent = `(message deleted by ${parsedContent.deleted_by || 'a user'})`;

                                const dismissBtn = document.createElement('span');
                                dismissBtn.className = 'dismiss-deleted-btn';
                                dismissBtn.innerHTML = '&times;';
                                dismissBtn.title = 'Dismiss';
                                dismissBtn.onclick = async () => {
                                    placeholder.remove();
                                    try {
                                        await fetch(`/api/messages/${message_id}/hide`, {
                                            method: 'DELETE',
                                            headers: { 'X-CSRF-Token': window.csrfTokenRaw }
                                        });
                                    } catch (error) {
                                        console.error('Failed to save hidden state:', error);
                                    }
                                };

                                placeholder.appendChild(text);
                                placeholder.appendChild(dismissBtn);
                                elementToUpdate.replaceWith(placeholder);

                            } else {
                                // This block handles other types of updates, like future AI corrections.
                                // For now, we'll just log it.
                                console.log("Received a non-delete message update:", messageData.payload);
                            }
                        } catch (e) {
                            // If it's not valid JSON, it's likely a regular content update (e.g. from an edit).
                            // This part of the logic can be expanded later.
                            console.log("Received a non-JSON message update, which may be an edit.");
                        }
                    }
                    break;
                }

                case 'message_deleted':
                    // This case is now handled by 'message_updated', but we'll leave it
                    // to gracefully handle any old events that might still be in flight.
                    break;
                default: handleStructuredMessage(messageData); break;
            }
        };
        ws.onclose = (event) => { console.error(`[WS_CLIENT] WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}`); };
        ws.onerror = (event) => { console.error("[WS_CLIENT] WebSocket error:", event); };
    } catch (error) {
        console.error("WebSocket creation error:", error);
    }
}

function createDeleteButton(messageId, messageUserId, turnContainer) {
    // --- START LOGGING ---
    console.log(`[CREATE DELETE BTN] Creating delete button for Message ID: ${messageId} (Type: ${typeof messageId})`);
    // --- END LOGGING ---

    const isHost = window.isHost === true;
    const isMyMessage = window.currentUserInfo && window.currentUserInfo.id === messageUserId;
    const isAiMessage = !messageUserId;

    if (!isHost && !isMyMessage && !isAiMessage) {
        return null;
    }

    const container = document.createElement('div');
    container.className = 'delete-btn-container';

    const button = document.createElement('button');
    button.className = 'delete-message-btn';
    button.innerHTML = '&times;';
    button.title = 'Delete Message';

    button.addEventListener('click', (e) => {
        e.stopPropagation();
        if (confirm('Are you sure you want to permanently delete this message? This cannot be undone.')) {
            if (websocket && websocket.readyState === WebSocket.OPEN) {
                // --- START LOGGING ---
                console.log(`[DELETE CLICK] Button clicked. Sending delete request for Message ID: ${messageId} (Type: ${typeof messageId})`);
                // --- END LOGGING ---
                websocket.send(JSON.stringify({
                    type: 'delete_message',
                    payload: { message_id: messageId }
                }));
            }
        }
    });

    container.appendChild(button);
    return container;
}

async function loadAndDisplayChatHistory(sessionId) {
    await updateAndDisplayParticipants();
    const chatHistoryDiv = document.getElementById('chat-history');
    if (!chatHistoryDiv) return;

    chatHistoryDiv.innerHTML = '<p class="text-center text-gray-500 p-4">Loading history...</p>';

    try {
        const [messagesResponse, editedBlocksResponse] = await Promise.all([
            fetch(`/api/sessions/${sessionId}/messages`),
            fetch(`/api/sessions/${sessionId}/edited-blocks`)
        ]);

        if (!messagesResponse.ok) {
            const errorData = await messagesResponse.json().catch(() => ({ detail: "Failed to load chat history." }));
            throw new Error(errorData.detail || messagesResponse.statusText);
        }

        const messages = await messagesResponse.json();
        const editedCodeBlocks = editedBlocksResponse.ok ? await editedBlocksResponse.json() : {};
        
        chatHistoryDiv.innerHTML = '';

        if (messages.length === 0) {
            chatHistoryDiv.innerHTML = '<p class="text-center text-gray-500 p-4">No sessions in this session yet.</p>';
        } else {
            currentTurnId = Math.max(0, ...messages.map(msg => msg.turn_id || 0));
            messages.forEach(msg => {
                renderSingleMessage(msg, chatHistoryDiv, true, editedCodeBlocks);
            });

            const projectContainers = Array.from(chatHistoryDiv.querySelectorAll('.ai-turn-container[data-project-name]'));
            const latestProjects = new Map();

            projectContainers.forEach(container => {
                const projectName = container.dataset.projectName;
                if (projectName) {
                    latestProjects.set(projectName, container);
                }
            });

            projectContainers.forEach(container => {
                const projectName = container.dataset.projectName;
                if (projectName && latestProjects.get(projectName) !== container) {
                    const explorer = container.querySelector('project-explorer');
                    const codeArea = container.querySelector('.code-blocks-area');
                    if (explorer) explorer.style.display = 'none';
                    if (codeArea) codeArea.style.display = 'none';
                    
                    const messageBubble = container.querySelector('.message.ai-message, .system-message');
                    if (!messageBubble) {
                        container.style.display = 'none';
                    }
                }
            });
        }
        
        chatHistory.scrollTop = chatHistory.scrollHeight;

    } catch (error){
        console.error(`Failed to fetch or display chat history for session ${sessionId}:`, error);
        chatHistoryDiv.innerHTML = `<p class="text-center text-red-500 p-4">An error occurred while loading history: ${escapeHTML(error.message)}</p>`;
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    await initializeCurrentUser();
    setInputDisabledState(true, false);

    if (typeof marked !== 'undefined' && typeof marked.setOptions === 'function') {
        marked.setOptions({
            gfm: true, breaks: true, sanitize: false, smartLists: true, smartypants: false,
        });
    }

    document.addEventListener('versionChanged', (event) => {
        const { turnContainer, files } = event.detail;
        if (!turnContainer) return;

        const codeBlocksArea = turnContainer.querySelector('.code-blocks-area');
        if (!codeBlocksArea) return;

        codeBlocksArea.innerHTML = '';

        const turnId = turnContainer.dataset.turnId;
        const projectDataStr = turnContainer.dataset.projectData;
        const promptingUserId = projectDataStr ? JSON.parse(projectDataStr).prompting_user_id : null;

        const outputLogFile = files.find(f => f.path.endsWith('_run_output.log'));
        const outputContent = outputLogFile ? outputLogFile.content : null;

        let runBlockId = null;

        files.forEach((file, index) => {
            if (file.path.endsWith('_run_output.log')) {
                return;
            }
            
            const codeBlockIndex = index + 1;
            const isRunnable = file.path.endsWith('run.sh');
            const block = createCodeBlock(
                file.language, file.content, file.content,
                turnId, codeBlockIndex, codeBlocksArea,
                isRunnable, promptingUserId
            );

            if (block) {
                const stableId = `code-block-turn${turnId}-${btoa(file.path)}`;
                block.id = stableId;
                block.dataset.path = file.path;
                block.querySelector('.block-title .title-text').textContent = file.path;

                if (isRunnable) {
                    runBlockId = block.id;
                }

                initializeCodeBlockHistory(block.id, file.content);

                if (file.checked === false) {
                    block.style.display = 'none';
                }
            }
        });

        if (runBlockId && outputContent !== null) {
            const runBlockTitle = "run.sh";
            const outputContainer = createOrClearOutputContainer(runBlockId, runBlockTitle, promptingUserId);
            const outputPre = outputContainer.querySelector('pre');
            
            if (outputPre) {
                outputPre.innerHTML = '';
                const outputSpan = document.createElement('span');
                outputSpan.className = 'stdout-output';
                outputSpan.textContent = outputContent;
                outputPre.appendChild(outputSpan);
            }
            updateHeaderStatus(outputContainer, 'Finished (from history)', 'success');
        }
    });

    if (chatForm && messageInput && sendButton && stopAiButton) {
        let typingTimeout;
        const sendTypingSignal = (isTyping) => {
            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send(JSON.stringify({ type: 'user_typing', payload: { is_typing: isTyping } }));
            }
        };

        messageInput.addEventListener('input', () => {
            if (messageInput.value.trim() === '' && typingTimeout) {
                clearTimeout(typingTimeout);
                typingTimeout = null;
                sendTypingSignal(false);
                return;
            }
            if (!typingTimeout) {
                sendTypingSignal(true);
            }
            clearTimeout(typingTimeout);
            typingTimeout = setTimeout(() => {
                sendTypingSignal(false);
                typingTimeout = null;
            }, 2000);
        });

        chatForm.addEventListener('submit', (event) => {
            event.preventDefault();
            if (typingTimeout) {
                clearTimeout(typingTimeout);
                typingTimeout = null;
                sendTypingSignal(false);
            }
            const userMessage = messageInput.value.trim();
            if (!userMessage) return;

            if (!websocket || websocket.readyState !== WebSocket.OPEN) {
                addErrorMessage("Cannot send message: Not connected to the server. Please refresh the page.");
                return;
            }

            currentTurnId++;
            const mentionRegex = /@(\w+)/gi;
            const recipients = (userMessage.match(mentionRegex) || []).map(m => m.substring(1).toUpperCase());

            // --- START FIX: Optimistically render a temporary message ---
            const tempId = `temp-id-${Date.now()}`;
            const tempUserMessage = {
                id: tempId, // Use a unique temporary ID
                sender_type: 'user',
                content: userMessage,
                turn_id: currentTurnId,
                sender_name: window.currentUserInfo ? window.currentUserInfo.name : "You",
                user_id: window.currentUserInfo ? window.currentUserInfo.id : null,
                sender_color: window.currentUserInfo ? window.currentUserInfo.color : '#e5e7eb',
                timestamp: new Date().toISOString()
            };

            console.log(`[SUBMIT] Optimistically rendering temporary message. Temp ID: ${tempId}, Turn ID: ${currentTurnId}`);
            
            renderSingleMessage(tempUserMessage, chatHistory, false, {});
            const tempMsgElement = document.querySelector(`[data-message-id='${tempId}']`);
            if (tempMsgElement) {
                // Add a special attribute to find and replace it later
                tempMsgElement.dataset.turnId = currentTurnId;
            }
            scrollToBottom('smooth');
            // --- END FIX ---

            if (recipients.includes("AI")) {
                if (!window.isAiConfigured) {
                    alert("AI provider has not been configured. Please go to User Settings to select a provider and add your API key.");
                    currentTurnId--;
                    if (tempMsgElement) tempMsgElement.remove(); // Clean up optimistic message on error
                    return;
                }
                
                handleAiThinking({
                    turn_id: currentTurnId,
                    prompting_user_id: window.currentUserInfo ? window.currentUserInfo.id : null,
                    prompting_user_name: window.currentUserInfo ? window.currentUserInfo.name : "User"
                });
            }

            try {
                messageInput.value = '';
                const messagePayload = {
                    type: "chat_message",
                    payload: {
                        user_input: userMessage,
                        turn_id: currentTurnId,
                        recipient_ids: recipients,
                        reply_to_id: null
                    }
                };
                websocket.send(JSON.stringify(messagePayload));
            } catch (sendError) {
                console.error("Error during message send:", sendError);
                addErrorMessage(`Failed to send message: ${sendError.message}`);
            }
        });

        stopAiButton.addEventListener('click', () => {
            if (!websocket || websocket.readyState !== WebSocket.OPEN) return;
            websocket.send(JSON.stringify({
                type: "stop_ai_stream",
                payload: { client_id: clientId, session_id: getSessionIdFromPath(), turn_id: currentTurnId }
            }));
        });
    }

    const currentSessionId = getSessionIdFromPath();
    if (currentSessionId) {
        await loadAndDisplayChatHistory(currentSessionId);
        connectWebSocket(currentSessionId);
    } else {
        if (messageInput) {
            setInputDisabledState(true, false);
        }
    }

    const chatHistoryScroller = document.getElementById('chat-history');
    if (chatHistoryScroller) {
        let rafId = null;
        const handleScroll = function() {
            if (rafId) cancelAnimationFrame(rafId);
            rafId = requestAnimationFrame(function() {
                const scrollerRect = chatHistoryScroller.getBoundingClientRect();
                const containers = chatHistoryScroller.querySelectorAll('.block-container');
                const measurements = [];
                containers.forEach(function(container) {
                    const header = container.querySelector('.block-header, .block-header');
                    const content = container.querySelector('pre, .block-output-console');
                    if (header && content) {
                        measurements.push({
                            header: header,
                            headerRect: header.getBoundingClientRect(),
                            contentRect: content.getBoundingClientRect(),
                            container: container
                        });
                    }
                });
                measurements.forEach(function(m) {
                    const shouldStick = m.headerRect.top < scrollerRect.top && m.contentRect.bottom > scrollerRect.top;
                    if (shouldStick) {
                        stickHeader(m.header, scrollerRect);
                        const stickyId = m.header.dataset.stickyId;
                        if (stickyId) {
                            const stickyClone = document.getElementById(stickyId);
                            if (stickyClone) {
                                const headerHeight = stickyClone.offsetHeight;
                                const contentBottom = m.contentRect.bottom;
                                const viewportTop = scrollerRect.top + 1;
                                const availableSpace = contentBottom - viewportTop + 3;
                                if (availableSpace < headerHeight && availableSpace > 0) {
                                    const clipAmount = headerHeight - availableSpace - 1;
                                    stickyClone.style.top = `${viewportTop - clipAmount - 1}px`;
                                    stickyClone.style.clipPath = `inset(${clipAmount}px 0 0 0)`;
                                    stickyClone.style.height = `${headerHeight}px`;
                                    stickyClone.style.borderBottomLeftRadius = '0.375rem';
                                    stickyClone.style.borderBottomRightRadius = '0.375rem';
                                } else if (availableSpace <= 2) {
                                    stickyClone.style.display = 'none';
                                } else {
                                    stickyClone.style.height = 'auto';
                                    stickyClone.style.top = `${scrollerRect.top}px`;
                                    stickyClone.style.display = 'flex';
                                    stickyClone.style.clipPath = 'none';
                                    stickyClone.style.borderBottomLeftRadius = '0';
                                    stickyClone.style.borderBottomRightRadius = '0';
                                }
                            }
                        }
                    } else {
                        unstickHeader(m.header);
                    }
                });
            });
        };
        chatHistoryScroller.addEventListener('scroll', function(e) {
            if (e.target === chatHistoryScroller) {
                handleScroll();
            }
        }, { passive: true });
    }
});

