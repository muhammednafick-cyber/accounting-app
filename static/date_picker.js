/*
 * One date picker for the whole application - the Receipt voucher's.
 *
 * Every date field goes through here, so the calendar looks and behaves the
 * same everywhere and a change made in this file reaches every screen:
 *
 *   - flatpickr, with its standard calendar
 *   - shown and typed as DD-MM-YYYY
 *   - typing allowed as well as picking
 *   - on close, the field fires `input` and `change`, so anything listening to
 *     it (due dates, totals, filters) hears about the new date
 *
 * Two kinds of field use it:
 *
 *   Text fields (DD-MM-YYYY) - the vouchers, most reports. They were already
 *   the Receipt picker; now they get it from here.
 *
 *   Native <input type="date"> fields - these send YYYY-MM-DD to the server,
 *   and that must not change. flatpickr keeps the value in the original field
 *   in exactly that format and shows a DD-MM-YYYY field in its place, so the
 *   person sees and types the same thing as on Receipt while the server
 *   receives the same thing as before.
 *
 * Use:
 *   AppDatePicker.attach('#from_date')   // one field, or any selector
 *   AppDatePicker.setDate(field, date)   // set a date on any date field
 * Fields marked as dates are picked up on their own when the page loads.
 */
(function () {
    'use strict';

    var DISPLAY_FORMAT = 'd-m-Y';           // what people see and type
    var NATIVE_FORMAT = 'Y-m-d';            // what <input type="date"> submits
    var TEXT_DATE_PATTERN = '\\d{2}-\\d{2}-\\d{4}';

    function tell(el) {
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function isNativeDate(el) {
        return (el.getAttribute('type') || '').toLowerCase() === 'date';
    }

    // The Receipt voucher's settings. Change the calendar here, not per page.
    function optionsFor(el) {
        var options = {
            dateFormat: DISPLAY_FORMAT,
            allowInput: true,
            onClose: function (selectedDates, dateStr, instance) {
                tell(instance.element);
            }
        };
        if (isNativeDate(el)) {
            options.dateFormat = NATIVE_FORMAT;
            options.altInput = true;
            options.altFormat = DISPLAY_FORMAT;
            // The visible field keeps the original's classes, so it sits in
            // its form exactly where the old one did.
            var classes = Array.prototype.filter.call(el.classList, function (c) {
                return c !== 'app-date' && c !== 'flatpickr-input';
            });
            classes.push('app-date');
            options.altInputClass = classes.join(' ');
            if (el.min) options.minDate = el.min;
            if (el.max) options.maxDate = el.max;
        }
        return options;
    }

    function toList(target) {
        if (!target) return [];
        if (typeof target === 'string') return Array.prototype.slice.call(document.querySelectorAll(target));
        if (target.nodeType === 1) return [target];
        return Array.prototype.slice.call(target);
    }

    function attach(target) {
        var instances = toList(target).map(function (el) {
            if (el._flatpickr) return el._flatpickr;          // already done
            if (typeof window.flatpickr === 'undefined') return null;
            // A native date field silently drops anything that is not a date -
            // a template printing "None", say. It still sits in the markup,
            // though, and once flatpickr turns the field into a hidden input
            // that text comes back and would be submitted. Drop it the same
            // way the browser did. (el.value already reads "" here, so the
            // markup is what has to be checked.)
            var raw = el.getAttribute('value');
            if (isNativeDate(el) && raw && !/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
                el.removeAttribute('value');
                el.value = '';
            }
            el.classList.add('app-date');
            var instance = window.flatpickr(el, optionsFor(el));
            if (instance && instance.altInput && el.required) {
                instance.altInput.required = true;             // keep the browser's check
            }
            return instance;
        });
        return instances.length === 1 ? instances[0] : instances;
    }

    function pad(n) { return (n < 10 ? '0' : '') + n; }

    // Today, or any date, in local time - not UTC, which in the UAE is still
    // yesterday until 4 in the morning.
    function setDate(el, date) {
        if (!el || !date) return;
        if (el._flatpickr) {
            el._flatpickr.setDate(date, true);
            return;
        }
        var d = pad(date.getDate()), m = pad(date.getMonth() + 1), y = date.getFullYear();
        el.value = isNativeDate(el) ? (y + '-' + m + '-' + d) : (d + '-' + m + '-' + y);
    }

    function isDateField(el) {
        if (el.tagName !== 'INPUT') return false;
        if (isNativeDate(el)) return true;
        if (el.classList.contains('date-picker')) return true;
        if (el.getAttribute('placeholder') === 'DD-MM-YYYY') return true;
        return el.getAttribute('pattern') === TEXT_DATE_PATTERN;
    }

    function attachAll(root) {
        var fields = Array.prototype.filter.call(
            (root || document).querySelectorAll('input'), isDateField);
        fields.forEach(function (el) { attach(el); });
        return fields.length;
    }

    window.AppDatePicker = {
        attach: attach,
        attachAll: attachAll,
        setDate: setDate,
        isDateField: isDateField,
        DISPLAY_FORMAT: DISPLAY_FORMAT
    };

    document.addEventListener('DOMContentLoaded', function () { attachAll(document); });
})();
