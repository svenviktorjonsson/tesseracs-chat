
const projectExplorerTemplate = document.createElement('template');
projectExplorerTemplate.innerHTML = `
    <style>
        :host {
            display: block;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            font-size: 14px;
            margin-top: 0.5rem;
            width: 100%;
            max-width: 90%;
        }
        .block-container {
            border-radius: 0.375rem;
            overflow: hidden;
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
            border: 1px solid #d1d5db;
        }
        .block-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: linear-gradient(to right, #e0f2fe, #d1fae5);
            padding: 0.5rem 0.75rem;
            color: #1f2937;
            border-bottom: 1px solid #d1d5db;
        }
        .header-left, .header-right {
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        .block-title {
            font-weight: 600;
        }
        .header-btn {
            background-color: rgba(255, 255, 255, 0.7);
            border: 1px solid #9ca3af;
            color: #374151;
            padding: 0.25rem 0.5rem;
            border-radius: 0.25rem;
            cursor: pointer;
            font-size: 0.8rem;
            transition: background-color 0.2s;
        }
        .header-btn:hover { background-color: rgba(255, 255, 255, 1); }
        #commit-select {
            font-size: 0.8rem;
            padding: 0.15rem 0.5rem;
            border-radius: 0.25rem;
            background-color: rgba(255, 255, 255, 0.7);
            border: 1px solid #9ca3af;
            min-width: 180px;
            max-width: 450px;
            box-sizing: border-box;
            height: 28px;
        }
        .tree-grid-header {
            display: grid;
            grid-template-columns: 1fr 180px 100px 100px;
            background-color: #f3f4f6;
            font-weight: 600;
            font-size: 0.75rem;
            text-transform: uppercase;
            color: #4b5563;
            padding: 0.25rem 0.5rem;
            border-bottom: 1px solid #d1d5db;
        }
        .header-name { padding-left: 2rem; }
        .tree-container {
            max-height: 350px;
            overflow-y: auto;
        }
        ul.tree, ul.tree ul {
            list-style-type: none;
            padding-left: 0;
            margin: 0;
        }
        ul.tree ul { padding-left: 20px; }
        li.folder.collapsed > ul { display: none; }
        .tree-item {
            display: grid;
            grid-template-columns: 1fr 180px 100px 100px;
            align-items: center;
            padding: 0.2rem 0.5rem;
            cursor: pointer;
            border-bottom: 1px solid #f3f4f6;
        }
        .tree-item:hover { background-color: #eff6ff; }
        .tree-item.drag-over { background-color: #dbeafe; }
        .item-name { display: flex; align-items: center; gap: 0.3rem; }
        .arrow-icon, .icon {
            width: 16px;
            height: 16px;
            stroke-width: 1.5;
            color: #6b7280;
            flex-shrink: 0;
        }
        li.folder.collapsed .arrow-icon { transform: rotate(-90deg); }
        .item-modified, .item-size { font-size: 0.8rem; color: #4b5563; }
        .item-show { text-align: center; }
        .drag-over-body { border: 2px dashed #3b82f6; }
    </style>
    <div class="block-container">
        <div class="block-header">
            <div class="header-left">
                <span class="block-title">Project Files</span>
                <button id="download-btn" class="header-btn">Download .zip</button>
                <button id="upload-btn" class="header-btn">Upload File</button>
                <input type="file" id="file-upload" style="display: none;" multiple>
            </div>
            <div class="header-right">
                <select id="commit-select"></select>
            </div>
        </div>
        <div id="tree-container"></div>
    </div>
`;
class ProjectExplorer extends HTMLElement {
    constructor() {
        super();
        this.attachShadow({ mode: 'open' });
        this.shadowRoot.appendChild(projectExplorerTemplate.content.cloneNode(true));
        
        this._projectData = null;
        this.selectedFolderPath = './';
    }

    connectedCallback() {
        this.bindEvents();
        if (this._projectData) {
            this.render();
        }
    }

    set projectData(data) {
        this._projectData = data;
        if (this.shadowRoot.getElementById('tree-container')) {
            this.render();
        }
    }

    get projectData() {
        return this._projectData;
    }

    addFile(fileData) {
        if (!this._projectData || !this._projectData.files) return;
        this._projectData.files.push(fileData);
        this.render();
    }

    updateFileSize(filePath, newSize) {
        const file = this._projectData.files.find(f => f.path === filePath);
        if (file) {
            file.size = newSize;
        }
        const fileRow = this.shadowRoot.querySelector(`li[data-path="${filePath}"] .item-size`);
        if (fileRow) {
            fileRow.textContent = this.formatBytes(newSize);
        }
    }

    updateData(newData) {
        console.log("ProjectExplorer component is updating with new data:", newData);
        this.projectData = newData;
    }
    
    render() {
        if (!this._projectData || !this.shadowRoot) return;

        const uploadButton = this.shadowRoot.getElementById('upload-btn');
        const downloadButton = this.shadowRoot.getElementById('download-btn');
        const hasProjectId = this._projectData.projectId || this._projectData.id;

        uploadButton.disabled = !hasProjectId;
        downloadButton.disabled = !hasProjectId;
        if (!hasProjectId) {
            uploadButton.title = "Available after project is saved";
            downloadButton.title = "Available after project is saved";
        } else {
            uploadButton.title = "Upload File";
            downloadButton.title = "Download .zip";
        }

        this.shadowRoot.querySelector('.block-title').textContent = `Project Files: ${this._projectData.projectName || ''}`;
        
        const treeContainer = this.shadowRoot.getElementById('tree-container');
        treeContainer.innerHTML = `
            <div class="tree-grid-header">
                <div class="header-name">Name</div>
                <div class="header-modified">Last Modified</div>
                <div class="header-size">Size</div>
                <div class="header-show">Show in Chat</div>
            </div>`;
        
        const fileTree = this.buildFileTree(this._projectData.files);
        const rootUl = document.createElement('ul');
        rootUl.className = 'tree';
        this.renderTree(fileTree, rootUl, './');
        treeContainer.appendChild(rootUl);

        const commitSelect = this.shadowRoot.getElementById('commit-select');
        commitSelect.innerHTML = '';
        (this._projectData.commits || []).forEach((commit, index) => {
            const option = document.createElement('option');
            option.value = commit.hash;
            option.textContent = `${commit.hash.substring(0, 7)} - ${commit.message}`;
            if (index === 0) {
                option.selected = true;
            }
            commitSelect.appendChild(option);
        });

        commitSelect.removeEventListener('change', this._commitChangeHandler);
        this._commitChangeHandler = async (event) => {
            const selectedCommitHash = event.target.value;
            const projectId = this._projectData.projectId || this._projectData.id;
            if (!selectedCommitHash || !projectId) return;

            try {
                const checkedStates = new Map();
                if (this._projectData && this._projectData.files) {
                    this._projectData.files.forEach(file => {
                        const checkbox = this.shadowRoot.querySelector(`input[data-path="${file.path}"]`);
                        if (checkbox) {
                            checkedStates.set(file.path, checkbox.checked);
                        }
                    });
                }

                const response = await fetch(`/api/projects/${projectId}/commit/${selectedCommitHash}`);
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || `Server responded with status ${response.status}`);
                }
                const newFiles = await response.json();
                
                newFiles.forEach(file => {
                    file.checked = checkedStates.has(file.path) ? checkedStates.get(file.path) : false;
                });

                this._projectData.files = newFiles;
                this.render();

                this.shadowRoot.getElementById('commit-select').value = selectedCommitHash;
                
                const turnContainer = this.closest('.ai-turn-container');
                this.dispatchEvent(new CustomEvent('versionChanged', { 
                    detail: { 
                        turnContainer: turnContainer,
                        files: newFiles
                    }, 
                    bubbles: true, 
                    composed: true 
                }));
                
            } catch (error) {
                console.error("Error fetching project version:", error);
                alert(`Could not load the selected project version: ${error.message}`);
            }
        };
        commitSelect.addEventListener('change', this._commitChangeHandler);
    }

    bindEvents() {
        const treeContainer = this.shadowRoot.getElementById('tree-container');
        let draggedElement = null;

        treeContainer.addEventListener('click', (event) => {
            const target = event.target;
            const li = target.closest('li');
            if (!li) return;

            if (target.type === 'checkbox') {
                const isChecked = target.checked;
                const path = target.dataset.path;
                const type = li.dataset.type;
                li.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = isChecked);
                
                const turnContainer = this.closest('.ai-turn-container');
                this.dispatchEvent(new CustomEvent('fileToggle', { 
                    detail: { 
                        path, 
                        isChecked, 
                        recursive: type === 'folder',
                        turnContainer: turnContainer 
                    }, 
                    bubbles: true, 
                    composed: true 
                }));
                return;
            }

            const itemDiv = target.closest('.tree-item');
            if (li.dataset.type === 'folder' && itemDiv) {
                li.classList.toggle('collapsed');
            }
        });

        treeContainer.addEventListener('dragstart', (e) => {
            const li = e.target.closest('li');
            if (li) {
                draggedElement = li;
                e.dataTransfer.setData('text/mpld3-filepath', li.dataset.path);
                e.dataTransfer.effectAllowed = 'move';
            }
        });

        treeContainer.addEventListener('dragover', (e) => {
            e.preventDefault();
            const targetLi = e.target.closest('li.folder');
            this.shadowRoot.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
            if (targetLi && draggedElement && targetLi !== draggedElement && !draggedElement.contains(targetLi)) {
                targetLi.querySelector('.tree-item').classList.add('drag-over');
            }
        });

        treeContainer.addEventListener('dragleave', (e) => {
            if (!treeContainer.contains(e.relatedTarget)) {
                this.shadowRoot.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
            }
        });

        this.shadowRoot.addEventListener('drop', (e) => {
            e.preventDefault();
            e.stopPropagation();
            this.shadowRoot.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));

            const internalMovePath = e.dataTransfer.getData('text/mpld3-filepath');
            const dropTarget = e.target.closest('li.folder');
            const destinationPath = dropTarget ? dropTarget.dataset.path : './';

            if (internalMovePath) {
                if (draggedElement && dropTarget && draggedElement !== dropTarget && !draggedElement.contains(dropTarget)) {
                    this.dispatchEvent(new CustomEvent('fileMove', { detail: { projectId: this._projectData.projectId, sourcePath: internalMovePath, destinationPath }, bubbles: true, composed: true }));
                }
            } else if (e.dataTransfer.files.length > 0) {
                this.handleFileUploads(e.dataTransfer.files, destinationPath);
            }
            draggedElement = null;
        });

        this.shadowRoot.addEventListener('dragover', (e) => { e.preventDefault(); });

        this.shadowRoot.getElementById('download-btn').addEventListener('click', () => {
            this.dispatchEvent(new CustomEvent('downloadClicked', { detail: { projectId: this._projectData.projectId }, bubbles: true, composed: true }));
        });

        const fileUploadInput = this.shadowRoot.getElementById('file-upload');
        this.shadowRoot.getElementById('upload-btn').addEventListener('click', () => fileUploadInput.click());
        fileUploadInput.addEventListener('change', (e) => this.handleFileUploads(e.target.files, this.selectedFolderPath));
    }

    handleFileUploads(files, targetPath) {
        Array.from(files).forEach(file => {
            const reader = new FileReader();
            reader.onload = (e) => {
                this.dispatchEvent(new CustomEvent('fileUpload', {
                    detail: { projectId: this._projectData.projectId, path: targetPath, filename: file.name, content: e.target.result },
                    bubbles: true, composed: true
                }));
            };
            reader.readAsText(file);
        });
    }

    buildFileTree(files) {
        const tree = { name: 'root', type: 'folder', children: {} };
        if (!files) return tree;
        files.forEach(file => {
            const parts = file.path.replace(/^\.\//, '').split('/');
            let currentNode = tree;
            parts.forEach((part, index) => {
                if (!part) return;
                if (!currentNode.children[part]) {
                    const isFolder = index < parts.length - 1;
                    currentNode.children[part] = { name: part, type: isFolder ? 'folder' : 'file', children: isFolder ? {} : null, ...(isFolder ? {} : file) };
                }
                currentNode = currentNode.children[part];
            });
        });
        return tree;
    }

    renderTree(treeNode, parentElement, currentPath) {
        const ICONS = {
            folder: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>`,
            file: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path><polyline points="13 2 13 9 20 9"></polyline></svg>`,
            arrow: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><polyline points="6 9 12 15 18 9"></polyline></svg>`
        };
        
        Object.keys(treeNode.children).sort((a, b) => {
            const nodeA = treeNode.children[a];
            const nodeB = treeNode.children[b];
            if (nodeA.type !== nodeB.type) return nodeA.type === 'folder' ? -1 : 1;
            return a.localeCompare(b);
        }).forEach(name => {
            const node = treeNode.children[name];
            const newPath = `${currentPath}${name}${node.type === 'folder' ? '/' : ''}`;
            const li = document.createElement('li');
            li.dataset.path = newPath;
            li.dataset.type = node.type;
            if (node.type === 'folder') li.classList.add('folder');
            li.setAttribute('draggable', 'true');

            const lastModified = node.lastModified ? new Date(node.lastModified).toLocaleString() : '';
            const size = node.size ? this.formatBytes(node.size) : '';
            const isChecked = node.checked === undefined ? true : node.checked;

            li.innerHTML = `
                <div class="tree-item">
                    <div class="item-name">
                        <span class="arrow-icon">${node.type === 'folder' ? ICONS.arrow : ''}</span>
                        <span class="icon">${ICONS[node.type]}</span>
                        <span class="label" title="${newPath}">${name}</span>
                    </div>
                    <div class="item-modified">${lastModified}</div>
                    <div class="item-size">${size}</div>
                    <div class="item-show">
                        <input type="checkbox" data-path="${newPath}" ${isChecked ? 'checked' : ''} title="Show in chat">
                    </div>
                </div>
            `;
            parentElement.appendChild(li);

            if (node.type === 'folder') {
                const childUl = document.createElement('ul');
                li.appendChild(childUl);
                this.renderTree(node, childUl, newPath);
            }
        });
    }

    formatBytes(bytes, decimals = 2) {
        if (!bytes || bytes === 0) return '';
        const k = 1024;
        const dm = decimals < 0 ? 0 : decimals;
        const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
    }
}

customElements.define('project-explorer', ProjectExplorer);

