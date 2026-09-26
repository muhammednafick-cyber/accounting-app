"""The chat on phones and tablets.

Measured in a browser before the change, on a 375px phone: messages had 44% of
the screen, the send button and wide tables ran off the right edge, the text
box was 14px - iOS zooms the whole page for anything smaller than 16 - and the
switches were 13px targets. These pin what fixed each of those.
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def source(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def media_block(css, query):
    start = css.index("@media (%s){" % query, css.index("Chat on phones and tablets"))
    depth, i = 0, css.index("{", start)
    while True:
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start:i + 1]
        i += 1


class LayoutTests(unittest.TestCase):

    def setUp(self):
        self.css = source("static", "ui.css")

    def test_wide_content_scrolls_inside_its_bubble_at_every_size(self):
        # A grid column of plain 1fr grows to fit its widest child, which is
        # what pushed the send button off a phone screen.
        self.assertIn(".global-chat-window .rv-chat{grid-template-columns:minmax(0,1fr)}",
                      self.css)

    def test_the_text_box_is_never_small_enough_for_ios_to_zoom(self):
        tablet = media_block(self.css, "max-width:1024px")
        self.assertIn("rv-chat-input-bar input{font-size:16px", tablet)

    def test_a_phone_gets_the_whole_screen_and_follows_the_keyboard(self):
        phone = media_block(self.css, "max-width:600px")
        self.assertIn("width:100vw!important", phone)
        self.assertIn("height:var(--chat-vh,100dvh)", phone)
        self.assertIn("env(safe-area-inset-bottom)", phone)
        self.assertIn("viewport-fit=cover", source("templates", "base.html"))

    def test_touch_targets_are_finger_sized(self):
        tablet = media_block(self.css, "max-width:1024px")
        for rule in ("global-chat-ai-toggle{min-height:36px",
                     "global-chat-close{width:40px;height:40px",
                     ".btn{min-height:44px;min-width:44px"):
            self.assertIn(rule, tablet)

    def test_voucher_buttons_hide_only_in_general_chat_and_the_status_line_stays(self):
        tablet = media_block(self.css, "max-width:1024px")
        self.assertIn(".global-chat-window.is-general .rv-chat-actions", tablet)
        # The status line shares that row and carries the Agent's progress.
        self.assertIn(".global-chat-window.is-general .rv-drafts-label{display:none}", tablet)
        self.assertNotIn(".rv-drafts-meta{display:none", tablet)
        self.assertIn('<span class="rv-drafts-label">', source("templates", "base.html"))

    def test_desktop_is_left_alone(self):
        # Everything size-specific sits inside a max-width query.
        block = self.css[self.css.index("Chat on phones and tablets"):]
        outside = re.sub(r"@media \([^)]*\)\{(?:[^{}]|\{[^{}]*\})*\}", "", block)
        self.assertNotIn("is-general", outside)
        self.assertNotIn("100vw", outside)


class ScriptTests(unittest.TestCase):

    def setUp(self):
        self.js = source("static", "script.js")

    def test_the_chat_marks_its_mode_for_the_stylesheet(self):
        self.assertIn("win.classList.toggle('is-general', !assistantState.voucherType)", self.js)
        body = self.js[self.js.index("function setVoucherTypeFromSlug"):]
        self.assertIn("markChatMode();", body[:body.index("\n    }\n")])

    def test_the_page_behind_is_locked_while_the_chat_is_open(self):
        self.assertIn("document.body.classList.add('chat-open')", self.js)
        self.assertIn("document.body.classList.remove('chat-open')", self.js)

    def test_the_height_follows_the_keyboard(self):
        self.assertIn("window.visualViewport", self.js)
        self.assertIn("'--chat-vh'", self.js)

    def test_browsers_are_told_all_three_files_changed(self):
        base = source("templates", "base.html")
        self.assertIn("filename='style.css') }}?v=20260923_3", base)
        self.assertIn("filename='script.js') }}?v=20260926_1", base)
        self.assertIn("filename='ui.css') }}?v=20260924_4", base)


class SendButtonTests(unittest.TestCase):
    """A paper plane on a phone, where the word squeezed the text box."""

    def test_the_button_keeps_its_name_for_screen_readers(self):
        base = source("templates", "base.html")
        self.assertIn('id="vaChatSendBtn" type="button" class="btn" aria-label="Send"', base)
        self.assertIn('<span class="send-label">Send</span>', base)
        self.assertIn('class="send-icon"', base)

    def test_the_icon_shows_only_on_a_phone(self):
        css = source("static", "ui.css")
        self.assertIn("#vaChatSendBtn .send-icon{display:none}", css)
        phone = css[css.index("Send: the word on wider screens"):]
        self.assertIn("#vaChatSendBtn .send-label{display:none}", phone)
        self.assertIn("#vaChatSendBtn .send-icon{display:block", phone)
        self.assertIn("width:44px;height:44px", phone)

    def test_nothing_rewrites_the_button_text(self):
        # Setting textContent would wipe the icon out.
        js = source("static", "script.js")
        self.assertNotIn("sendBtn.textContent", js)
        self.assertNotIn("sendBtn.innerText", js)


class GreetingTests(unittest.TestCase):
    """The general-chat greeting stacked up: opening the window and picking
    General Chat each posted it, and opening ran both paths."""

    def test_the_greeting_has_one_source_and_a_guard(self):
        js = source("static", "script.js")
        text = "Hi! Ask me accounting questions or select a voucher type to create one."
        self.assertEqual(js.count(text), 1)
        body = js[js.index("function greetGeneralChat"):]
        body = body[:body.index("function markChatMode")]
        self.assertIn("if (messagesEl.dataset.vaWelcomeFor === 'general') return;", body)
        self.assertIn("messagesEl.dataset.vaWelcomeFor = 'general';", body)

    def test_nothing_posts_it_directly_any_more(self):
        js = source("static", "script.js")
        self.assertNotIn(
            "globalChatAppendMessage('bot', 'Hi! Ask me accounting questions", js.replace(
                js[js.index("function greetGeneralChat"):js.index("function markChatMode")], ""))


if __name__ == "__main__":
    unittest.main()
