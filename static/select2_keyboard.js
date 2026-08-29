/*
 * Keyboard-first dropdowns.
 *
 * Select2 on its own needs two mouse actions to search: click the control to
 * open it, then aim at the little search box inside the dropdown. On a data
 * entry screen with a dozen dropdowns that is a lot of mousing for something
 * the keyboard should do.
 *
 * This makes every Select2 on the page behave the way a till or a voucher
 * screen wants:
 *
 *   Tab into a dropdown  -> it opens with the search box already focused,
 *                           so you just start typing
 *   Start typing on it   -> opens and takes the first keystroke, nothing lost
 *   Enter                -> picks the highlighted match
 *   Tab                  -> picks the highlighted match and moves on
 *   Esc                  -> closes without choosing
 *
 * Applied globally through delegated handlers, so it covers dropdowns that are
 * created later (new voucher lines, POS rows) without any page having to opt
 * in.
 */
(function () {
    if (!window.jQuery || !window.jQuery.fn || !window.jQuery.fn.select2) return;
    var $ = window.jQuery;

    // Closing a Select2 hands focus back to its own control. Without this
    // guard that focus would re-open the dropdown, and it could never be
    // closed with the keyboard.
    var justClosed = false;

    $(document).on('select2:closing', function () {
        justClosed = true;
        setTimeout(function () { justClosed = false; }, 150);
    });

    // Select2 4.0.13 does not move focus into its search box when the
    // dropdown is opened from code, which is exactly how it is opened below.
    // Without this the first keystroke would land on the control and every
    // one after it would go nowhere.
    $(document).on('select2:open', function () {
        var search = document.querySelector(
            '.select2-container--open .select2-search__field');
        if (search) search.focus();
    });

    function selectFor(element) {
        // Select2 puts its container immediately after the original <select>.
        var $container = $(element).closest('.select2-container');
        var $select = $container.prev('select');
        return $select.length && $select.data('select2') ? $select : null;
    }

    // Tab lands on the control: open it, so the search box is already focused
    // and the next keystroke is a search rather than a wasted press.
    $(document).on('focus', '.select2-selection', function () {
        if (justClosed) return;
        var $select = selectFor(this);
        if ($select && !$select.data('select2').isOpen()) $select.select2('open');
    });

    // Typing on a closed control opens it and keeps that first character,
    // which is what people expect from a native <select>.
    $(document).on('keydown', '.select2-selection', function (event) {
        if (event.ctrlKey || event.altKey || event.metaKey) return;
        if (event.key === undefined || event.key.length !== 1) return;

        var $select = selectFor(this);
        if (!$select || $select.data('select2').isOpen()) return;

        event.preventDefault();
        $select.select2('open');
        // The open handler above has put focus in the search box; seed it with
        // the character that was just typed so nothing is lost.
        var $search = $('.select2-container--open .select2-search__field');
        $search.val(event.key).trigger('input');
    });

    // Tab out of an open dropdown takes the highlighted match rather than
    // throwing away what was typed - the same as Enter, without reaching for
    // the mouse to confirm.
    $(document).on('keydown', '.select2-search__field', function (event) {
        if (event.key !== 'Tab') return;
        var $highlighted = $('.select2-results__option--highlighted');
        if (!$highlighted.length) return;
        $highlighted.trigger('mouseup');   // how Select2 commits a choice
    });
}());
