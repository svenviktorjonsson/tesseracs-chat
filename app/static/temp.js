/**
 * Creates the DOM structure for a code block with separate headers
 * for code and output sections, placing the status span in the output header
 * with the final requested layout and aligning output buttons.
 */
function createCodeBlockStructure(language) {
    if (!currentCodeBlocksArea) {
        console.error("Code blocks area is null!");
        return;
    }
    codeBlockCounterThisTurn++;
    const currentCodeNumber = codeBlockCounterThisTurn; // Store for use in output header

    const blockId = `code-block-turn${currentTurnId}-${currentCodeNumber}`;
    const safeLanguage = (language || '').trim().toLowerCase() || 'plain';

    // Language mapping for PrismJS highlighting
    const langAlias = {
        'python': 'python', 'py': 'python',
        'javascript': 'javascript', 'js': 'javascript',
        'html': 'markup', 'xml': 'markup', 'svg': 'markup',
        'css': 'css',
        'bash': 'bash', 'sh': 'bash', 'shell': 'bash',
        'json': 'json',
        'yaml': 'yaml', 'yml': 'yaml',
        'markdown': 'markdown', 'md': 'markdown',
        'sql': 'sql',
        'java': 'java',
        'c': 'c',
        'cpp': 'cpp', 'c++': 'cpp',
        'csharp': 'csharp', 'cs': 'csharp',
        'go': 'go',
        'rust': 'rust',
        'php': 'php',
        'ruby': 'ruby', 'rb': 'ruby',
        'dockerfile': 'docker', 'docker': 'docker',
        'typescript': 'typescript', 'ts': 'typescript',
        'plaintext': 'plain', 'text': 'plain',
     };
    const prismLang = langAlias[safeLanguage] || safeLanguage;
    const displayLang = safeLanguage;

    // --- SVG Icons ---
    const playIconSvg = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;
    // Stop/Stopping SVGs defined locally in handlers where needed

    // --- Main Container ---
    const container = document.createElement('div');
    container.classList.add('code-block-container');
    container.id = blockId;
    container.dataset.language = safeLanguage;

    // --- Code Header ---
    const codeHeader = document.createElement('div');
    codeHeader.classList.add('code-block-header'); // Use existing class

    // Code Header - Buttons Div (Left)
    const codeButtonsDiv = document.createElement('div');
    codeButtonsDiv.classList.add('code-block-buttons');

    const runStopBtn = document.createElement('button');
    runStopBtn.classList.add('run-code-btn', 'code-action-btn');
    runStopBtn.dataset.status = 'idle';
    runStopBtn.innerHTML = playIconSvg;
    runStopBtn.title = 'Run Code';

    const toggleCodeBtn = document.createElement('button');
    toggleCodeBtn.classList.add('toggle-code-btn', 'code-action-btn');
    toggleCodeBtn.textContent = 'Hide';
    toggleCodeBtn.title = 'Show/Hide Code';

    const copyCodeBtn = document.createElement('button');
    copyCodeBtn.classList.add('copy-code-btn', 'code-action-btn');
    copyCodeBtn.textContent = 'Copy';
    copyCodeBtn.title = 'Copy Code';

    codeButtonsDiv.appendChild(runStopBtn);
    codeButtonsDiv.appendChild(toggleCodeBtn);
    codeButtonsDiv.appendChild(copyCodeBtn);

    // Code Header - Title (Takes remaining space)
    const codeTitle = document.createElement('span');
    codeTitle.classList.add('code-block-title');
    codeTitle.textContent = `Code ${currentCodeNumber} (${displayLang})`;
    codeTitle.style.flexGrow = '1';
    codeTitle.style.textAlign = 'left';

    // Assemble Code Header
    codeHeader.appendChild(codeButtonsDiv);
    codeHeader.appendChild(codeTitle);

    // --- Code Area ---
    const preElement = document.createElement('pre');
    preElement.classList.add('manual');
    const codeElement = document.createElement('code');
    codeElement.classList.add(`language-${prismLang}`);
    codeElement.setAttribute('contenteditable', 'true');
    codeElement.setAttribute('spellcheck', 'false');

    currentCodeBlockPreElement = preElement;
    currentCodeBlockElement = codeElement;

    // --- Output Header ---
    const outputHeader = document.createElement('div');
    outputHeader.classList.add('code-output-header');
    outputHeader.style.display = 'none'; // Initially hidden

    // Output Header - Buttons (Far Left) - Create first
    const outputButtonsDiv = document.createElement('div');
    // Reuse same class as code header buttons for consistent styling
    outputButtonsDiv.classList.add('code-block-buttons'); // CHANGED CLASS

    // ADD PLACEHOLDER SPAN for alignment
    const placeholderSpan = document.createElement('span');
    placeholderSpan.classList.add('output-header-button-placeholder');
    outputButtonsDiv.appendChild(placeholderSpan); // Add placeholder first

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

    // Output Header - Title (Middle) - Create second
    const outputTitle = document.createElement('span');
    outputTitle.classList.add('output-header-title');
    outputTitle.textContent = `Output Code ${currentCodeNumber}`;

    // Output Header - Status Span (Far Right) - Create third
    const codeStatusSpan = document.createElement('span');
    codeStatusSpan.classList.add('code-status-span');
    codeStatusSpan.textContent = 'Idle'; // Initial status

    // Assemble Output Header (NEW ORDER)
    outputHeader.appendChild(outputButtonsDiv); // Buttons first
    outputHeader.appendChild(outputTitle);      // Title next
    outputHeader.appendChild(codeStatusSpan); // Status last (will be pushed right by CSS)


    // --- Output Console Area ---
    const outputConsoleDiv = document.createElement('div');
    outputConsoleDiv.classList.add('code-output-console');
    outputConsoleDiv.style.display = 'none'; // Initially hidden
    const outputPre = document.createElement('pre');
    outputConsoleDiv.appendChild(outputPre);

    // --- Assemble Container ---
    preElement.appendChild(codeElement);
    container.appendChild(codeHeader);
    container.appendChild(preElement);
    container.appendChild(outputHeader); // Add output header
    container.appendChild(outputConsoleDiv);

    // --- Event Listeners ---
    toggleCodeBtn.addEventListener('click', () => {
        const isHidden = preElement.classList.toggle('hidden');
        toggleCodeBtn.textContent = isHidden ? 'Show' : 'Hide';
    });

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

    runStopBtn.addEventListener('click', handleRunStopCodeClick);

    // Listener for debounced highlighting on code edit
    const debouncedHighlight = debounce(() => {
         console.log(`Highlighting ${blockId} after edit.`);
        const savedPosition = getCursorPosition(codeElement);
        if (savedPosition === -1) { console.warn("Could not save cursor position or cursor not in element. Highlight may cause cursor jump."); }
        try {
            const tokens = codeElement.querySelectorAll('span[class*="token"]');
            tokens.forEach(span => {
                if (span.textContent) { span.replaceWith(document.createTextNode(span.textContent)); } else { span.remove(); }
            });
            codeElement.normalize();
            Prism.highlightElement(codeElement);
            if (savedPosition !== -1) { setCursorPosition(codeElement, savedPosition); }
        } catch (e) {
            console.error("Error during debounced highlighting:", e);
            if (savedPosition !== -1) { setCursorPosition(codeElement, savedPosition); }
        }
    }, 500);
    codeElement.addEventListener('input', debouncedHighlight);
    codeElement.addEventListener('paste', (e) => { setTimeout(debouncedHighlight, 100); });

    // Output Header Button Listeners
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

    // --- Append to DOM ---
    currentCodeBlocksArea.appendChild(container);
    lastAppendedNode = null;
}

// --- Other functions (handleRunStopCodeClick, connectWebSocket, etc.) remain unchanged ---
