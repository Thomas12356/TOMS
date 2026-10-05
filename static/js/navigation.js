// Keep links available without JavaScript; collapse them only on small screens.
(() => {
    const toggle = document.getElementById('navigation-toggle');
    const navigation = document.getElementById('main-navigation');
    if (!toggle || !navigation) return;
    const mobile = window.matchMedia('(max-width: 640px)');

    function setExpanded(expanded) {
        navigation.hidden = !expanded;
        toggle.setAttribute('aria-expanded', String(expanded));
    }

    function setLayout() {
        toggle.hidden = !mobile.matches;
        setExpanded(!mobile.matches);
    }

    toggle.addEventListener('click', () => setExpanded(navigation.hidden));
    navigation.addEventListener('keydown', event => {
        if (event.key === 'Escape' && mobile.matches) {
            setExpanded(false);
            toggle.focus();
        }
    });
    mobile.addEventListener('change', setLayout);
    setLayout();
})();
