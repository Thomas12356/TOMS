// Keep links available without JavaScript; collapse them only on small screens.
(() => {
    const toggle = document.getElementById('navigation-toggle');
    const navigation = document.getElementById('main-navigation');
    if (!toggle || !navigation) return;
    const mobile = window.matchMedia('(max-width: 900px)');
    const groups = [...navigation.querySelectorAll('details.navigation-group')];

    function closeGroups(except) {
        groups.forEach(group => { if (group !== except) group.open = false; });
    }

    groups.forEach(group => {
        // Activation has selected its target before siblings collapse. Native
        // toggle events arrive later and can otherwise close a newer menu.
        group.querySelector('summary').addEventListener('click', () => closeGroups(group));
        group.addEventListener('focusout', event => {
            // Closing a stacked menu on pointer focus would move the next
            // summary before its click finishes. Activation closes siblings.
            if (!mobile.matches && event.relatedTarget && !group.contains(event.relatedTarget)) group.open = false;
        });
    });
    document.addEventListener('pointerdown', event => {
        if (!navigation.contains(event.target)) closeGroups();
    });

    function setExpanded(expanded) {
        navigation.hidden = !expanded;
        toggle.setAttribute('aria-expanded', String(expanded));
    }

    function setLayout() {
        closeGroups();
        toggle.hidden = !mobile.matches;
        setExpanded(!mobile.matches);
    }

    toggle.addEventListener('click', () => setExpanded(navigation.hidden));
    navigation.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        const openGroup = groups.find(group => group.open);
        if (openGroup) {
            openGroup.open = false;
            openGroup.querySelector('summary').focus();
            event.preventDefault();
        } else if (mobile.matches) {
            setExpanded(false);
            toggle.focus();
        }
    });
    mobile.addEventListener('change', setLayout);
    setLayout();
})();
