"""Step 3 of docs/specs/refactor/discord_events_through_router_design_spec.md.

Check and Luck button clicks enter through the router. The end-to-end
claim/restore races are covered by tests/test_pending_button_latency.py; these
pin the early exits and the entry event.
"""
import unittest
from unittest.mock import AsyncMock, patch

from app import locks
from app.commands import router
from app.commands.handlers import buttons
from app.commands.handlers.buttons import ButtonIO


def _io() -> ButtonIO:
    return ButtonIO(
        notify=AsyncMock(), acknowledge=AsyncMock(), reply=AsyncMock(), send_dm=AsyncMock(),
        send_image=AsyncMock(), send_dm_image=AsyncMock(), restore_buttons=AsyncMock(),
    )


class ButtonEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_someone_elses_button_is_refused_before_anything_else(self):
        for handle in (router.handle_check_button, router.handle_luck_button):
            with self.subTest(handle=handle.__name__):
                io = _io()
                with patch.object(locks, "try_acquire_check") as acquire:
                    await handle("g", "intruder", "owner", "", "", io)
                io.notify.assert_awaited_once()
                acquire.assert_not_called()
                io.acknowledge.assert_not_awaited()

    async def test_a_click_while_a_check_is_in_flight_keeps_the_buttons(self):
        io = _io()
        with patch.object(locks, "try_acquire_check", return_value=False), \
                patch.object(locks, "release_check") as release:
            await router.handle_check_button("g", "owner", "owner", "", "c1", io)
        io.notify.assert_awaited_once_with(buttons._BUSY)
        io.acknowledge.assert_not_awaited()
        release.assert_not_called()

    async def test_a_stale_click_is_acknowledged_released_and_not_restored(self):
        io = _io()
        with patch.object(locks, "try_acquire_check", return_value=True), \
                patch.object(locks, "release_check") as release, \
                patch.object(buttons, "load_state", return_value=buttons.GroupState(group_id="g")):
            await router.handle_luck_button("g", "owner", "owner", "spend", "d1", io)
        io.acknowledge.assert_awaited_once()
        io.notify.assert_awaited_once_with("這個 Luck 決定已經結束或失效了。")
        io.restore_buttons.assert_not_awaited()
        release.assert_called_once_with("g", "owner")

    async def test_clicks_emit_their_router_entry(self):
        for handle, entry in ((router.handle_check_button, "check_button"), (router.handle_luck_button, "luck_button")):
            with self.subTest(entry=entry), patch.object(router.observability, "event") as event:
                await handle("g", "intruder", "owner", "", "", _io())
            event.assert_any_call("turn.entry", entry=entry)


if __name__ == "__main__":
    unittest.main()
