export function initializeSidebarToggle() {
    const sidebar = document.getElementById('sidebar-loader-target');
    const toggleButton = document.getElementById('sidebar-toggle');
    const toggleIcon = document.getElementById('sidebar-toggle-icon');

    if (!sidebar || !toggleButton || !toggleIcon) {
        console.error("Sidebar toggle elements not found.");
        return;
    }

    toggleButton.addEventListener('click', () => {
        sidebar.classList.toggle('sidebar-collapsed');
        toggleButton.classList.toggle('collapsed');
        toggleIcon.classList.toggle('collapsed');
    });
}
