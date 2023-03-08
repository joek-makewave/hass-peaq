import logging
import time

from peaqevcore.models.chargecontroller_states import ChargeControllerStates
from peaqevcore.models.chargertype.calltype_enum import CallTypes
from peaqevcore.services.session.session import Session

from custom_components.peaqev.peaqservice.charger.charger_states import ChargerStates
from custom_components.peaqev.peaqservice.charger.chargerhelpers import ChargerHelpers
from custom_components.peaqev.peaqservice.charger.chargerparams import ChargerParams
from custom_components.peaqev.peaqservice.chargertypes.models.chargertypes_enum import ChargerType
from custom_components.peaqev.peaqservice.util.constants import (
    DOMAIN,
    PARAMS,
    CURRENT
)

_LOGGER = logging.getLogger(__name__)
CALL_WAIT_TIMER = 60


async def _debug_log(debugmessage: str = None) -> None:
    if debugmessage is not None:
        _LOGGER.debug(debugmessage)


class Charger:
    def __init__(self, hub, chargertype):
        self.hub = hub
        self._charger = chargertype
        self.params = ChargerParams()
        self.session = Session(self)
        self.helpers = ChargerHelpers(self)
        self.hub.observer.add("power canary dead", self._pause_charger)
        self.hub.observer.add("chargecontroller status changed", self.charge)

    @property
    def session_active(self) -> bool:
        return self.params.session_active

    @session_active.setter
    def session_active(self, val):
        self.params.session_active = val

    @property
    def charger_active(self) -> bool:
        if self._charger.options.powerswitch_controls_charging:
            return self.hub.sensors.chargerobject_switch.value  # todo: composition
        return all(
            [
                self.hub.sensors.chargerobject_switch.value,  # todo: composition
                self.hub.sensors.carpowersensor.value > 0  # todo: composition
            ]
        )

    async def charge(self) -> None:
        """Main function to turn charging on or off"""
        if self._charger.type is ChargerType.NoCharger:
            return
        if self.params.charger_state_mismatch:
            await self._update_internal_state(ChargerStates.Pause)
        _controller = await self.hub.get_states_async("chargecontroller")
        _killswitch = await self.hub.get_states_async("killswitch")
        if self.hub.enabled and not _killswitch.get('is_dead'):
            await self._reset_session(_controller)
            if _controller:
                match _controller.get('status_type'):
                    case ChargeControllerStates.Start:
                        await self._start_case()
                    case ChargeControllerStates.Stop:
                        await self._stop_case()
                    case ChargeControllerStates.Done:
                        await self._done_case()
                    case ChargeControllerStates.Idle:
                        await self._terminate_charger() if self.charger_active else None
                    case _:
                        _LOGGER.debug(f"Could not match any chargecontroller-state. chargecontroller reports: {_controller.get('status_type')}")
        else:
            if self.charger_active and self.params.running:
                debugmsg = None
                if _killswitch.get('is_dead'):
                    debugmsg = f"Your powersensor has failed to update peaqev for more than {_killswitch.get('total_timer')} seconds. Therefore charging is paused until it comes alive again."
                elif self.hub.enabled:
                    debugmsg = "Detected charger running outside of peaqev-session, overtaking command and pausing."
                await self._pause_charger(_controller=_controller, debugmessage=debugmsg)

    async def _done_case(self) -> None:
        if not self.hub.charger_done:
            await self._terminate_charger(debugmessage="Going to terminate since the charger is done.")

    async def _stop_case(self) -> None:
        if self.charger_active:
            debugmsg = None
            if not self.params.running and not self.session_active:
                debugmsg = "Detected charger running outside of peaqev-session, overtaking command and pausing."
            await self._pause_charger(debugmsg)

    async def _start_case(self) -> None:
        if not self.params.running:
            if not self.charger_active:
                await self._start_charger()
            else:
                await self._overtake_charger(debugmessage="Detected charger running outside of peaqev-session, overtaking command.")

    async def _reset_session(self, _controller) -> None:
        if not self.session_active and _controller.get('status_type') is not ChargeControllerStates.Done:
            self.session.core.reset()
            self.session_active = True

    async def _overtake_charger(self, debugmessage: str = None) -> None:
        await _debug_log(debugmessage)
        await self._update_internal_state(ChargerStates.Start)
        self.session_active = True
        await self._post_start_charger()

    def _call_ok(self) -> bool:
        return time.time() - self.params.latest_charger_call > CALL_WAIT_TIMER

    async def _start_charger(self, debugmessage: str = None) -> None:
        await _debug_log(debugmessage)
        if self._call_ok():
            await self._update_internal_state(ChargerStates.Start)
            if not self.session_active:
                await self._call_charger(CallTypes.On)
                self.session_active = True
            else:
                await self._call_charger(CallTypes.Resume)
            await self._post_start_charger()

    async def _post_start_charger(self) -> None:
        #is this call really needed?
        self.hub.chargecontroller.latest_charger_start = time.time()  # todo: composition
        if self._charger.servicecalls.options.allowupdatecurrent and not self.hub.is_free_charge:
            self.hub.state_machine.async_create_task(self._updatemaxcurrent())

    async def _terminate_charger(self, debugmessage: str = None) -> None:
        await _debug_log(debugmessage)
        if self._call_ok():
            await self.hub.state_machine.async_add_executor_job(self.session.core.terminate)
            await self._update_internal_state(ChargerStates.Stop)
            self.session_active = False
            await self._call_charger(CallTypes.Off)
            self.hub.observer.broadcast("update charger done", True)

    async def _pause_charger(self, _controller, debugmessage: str = None) -> None:
        await _debug_log(debugmessage)
        if self._call_ok():
            if self.hub.charger_done or _controller.get('status_type') is ChargeControllerStates.Idle:
                await self._terminate_charger()
            else:
                await self._update_internal_state(ChargerStates.Pause)
                await self._call_charger(CallTypes.Pause)

    async def _call_charger(self, command: CallTypes) -> None:
        calls = self._charger.servicecalls.get_call(command)
        if self._charger.servicecalls.options.switch_controls_charger:  # todo: composition
            await self._do_outlet_update(calls.get(command))
        else:
            await self._do_service_call(calls.get(DOMAIN), calls.get(command), calls.get("params"))
        self.params.latest_charger_call = time.time()

    async def _updatemaxcurrent(self) -> None:
        self.hub.sensors.chargerobject_switch.updatecurrent()
        calls = self._charger.servicecalls.get_call(CallTypes.UpdateCurrent)
        if await self.hub.state_machine.async_add_executor_job(self.helpers.wait_turn_on):
            # call here to set amp-list
            while all([
                self.hub.sensors.chargerobject_switch.value,
                self.params.running
            ]):
                if await self.hub.state_machine.async_add_executor_job(self.helpers.wait_update_current):
                    serviceparams = await self.helpers.setchargerparams(calls)
                    if not self.params.disable_current_updates and self.hub.power_canary.allow_adjustment(
                            new_amps=serviceparams[calls[PARAMS][CURRENT]]):
                        await self._do_service_call(calls[DOMAIN], calls[CallTypes.UpdateCurrent], serviceparams)
                    await self.hub.state_machine.async_add_executor_job(self.helpers.wait_loop_cycle)

            if self._charger.servicecalls.options.update_current_on_termination is True:
                final_service_params = await self.helpers.setchargerparams(calls, ampoverride=6)
                await self._do_service_call(
                    calls[DOMAIN],
                    calls[CallTypes.UpdateCurrent],
                    final_service_params
                )

    async def _do_outlet_update(self, call):
        await _debug_log(f"Calling charger-outlet")
        await self.hub.state_machine.states.async_set(self._charger.entities.powerswitch, call)  # todo: composition

    async def _do_service_call(self, domain, command, params) -> None:
        await _debug_log(f"Calling charger {command} for domain '{domain}' with parameters: {params}")
        await self.hub.state_machine.services.async_call(
            domain,
            command,
            params
        )

    async def _update_internal_state(self, state: ChargerStates) -> None:
        if state in [ChargerStates.Start, ChargerStates.Resume]:
            await self._update_internal_state_on()
        elif state in [ChargerStates.Stop, ChargerStates.Pause]:
            await self._update_internal_state_off()

    async def _update_internal_state_on(self):
        self.params.running = True
        self.params.disable_current_updates = False
        await _debug_log("Internal charger has been started")

    async def _update_internal_state_off(self):
        self.params.disable_current_updates = True
        charger_state = self.hub.get_chargerobject_value()
        if any([
            charger_state not in self._charger.chargerstates.get(ChargeControllerStates.Charging),
            len(charger_state) < 1
        ]):
            self.params.running = False
            self.params.charger_state_mismatch = False
            await _debug_log("Internal charger has been stopped")
        else:
            self.params.charger_state_mismatch = True
            await _debug_log(f"Fail when trying to stop connected charger. Retrying stop-attempt...")


