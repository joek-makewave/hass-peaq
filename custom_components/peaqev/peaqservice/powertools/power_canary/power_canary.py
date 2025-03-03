from __future__ import annotations

import logging

from peaqevcore.models.fuses import Fuses
from peaqevcore.models.phases import Phases

from custom_components.peaqev.peaqservice.powertools.power_canary.const import (
    CRITICAL, CUTOFF_THRESHOLD, DISABLED, FUSES_MAX_SINGLE_FUSE, OK, WARNING,
    WARNING_THRESHOLD)
from custom_components.peaqev.peaqservice.powertools.power_canary.power_canary_model import \
    PowerCanaryModel
from custom_components.peaqev.peaqservice.powertools.power_canary.smooth_average import \
    SmoothAverage

_LOGGER = logging.getLogger(__name__)


class PowerCanary:
    def __init__(self, hub):
        self._enabled: bool = False
        self.hub = hub
        self.model = PowerCanaryModel(
            warning_threshold=WARNING_THRESHOLD,
            cutoff_threshold=CUTOFF_THRESHOLD,
            fuse=Fuses.parse_from_config(hub.options.fuse_type),
            allow_amp_adjustment=self.hub.chargertype.servicecalls.options.allowupdatecurrent,
        )
        self._total_power = SmoothAverage(max_age=60, max_samples=30, ignore=0)
        self._validate()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def alive(self) -> bool:
        """if returns false no charging can be conducted"""
        if self._enabled is False:
            return True
        return self.hub.sensors.power.total.value < self.model.fuse_max * self.model.cutoff_threshold

    @property
    def fuse(self) -> str:
        return self.model.fuse.value

    @property
    def current_percentage(self) -> float:
        try:
            return self._total_power.value / self.model.fuse_max
        except:
            return 0

    @property
    def total_power(self) -> float:
        if self.enabled:
            return self._total_power.value

    @total_power.setter
    def total_power(self, value) -> None:
        if self.enabled:
            self._total_power.add_reading(float(value))
            self.check_current_percentage()

    @property
    def state_string(self) -> str:
        if not self.enabled:
            return DISABLED
        if not self.alive:
            return CRITICAL
        if self.current_percentage >= self.model.warning_threshold:
            return WARNING
        return OK

    @property
    def onephase_amps(self) -> dict:
        ret = self._get_currently_allowed_amps(self.model.onephase_amps)
        return {k: v for (k, v) in ret.items() if v < FUSES_MAX_SINGLE_FUSE.get(self.model.fuse)}

    @property
    def threephase_amps(self) -> dict:
        return self._get_currently_allowed_amps(self.model.threephase_amps)

    def check_current_percentage(self):
        if not self.alive:
            self.hub.observer.broadcast(command="power canary dead")
        if self.current_percentage >= self.model.warning_threshold:
            self.hub.observer.broadcast(command="power canary warning")

    @property
    def max_current_amp(self) -> int:
        if not self.enabled:
            return -1
        match self.hub.threshold.phases:
            case Phases.OnePhase.name:
                return max(self.onephase_amps.values())
            case Phases.ThreePhase.name:
                return max(self.onephase_amps.values())
        return -1

    async def async_allow_adjustment(self, new_amps: int) -> bool:
        """this method returns true if the desired adjustment 'new_amps' is not breaching threshold"""
        if not self.enabled:
            return True
        if not self.model.allow_amp_adjustment:
            return False
        ret = new_amps <= self.max_current_amp
        if ret is False and self.max_current_amp > -1:
            _LOGGER.warning(
                f"Power Canary cannot allow amp-increase due to the current power-draw. max-amp is:{self.max_current_amp} "
            )
        return ret

    def _get_currently_allowed_amps(self, amps) -> dict:
        """get the currently allowed amps based on the current power draw"""
        _max = self.model.fuse_max * self.model.cutoff_threshold
        return {k: v for (k, v) in amps.items() if k + self.hub.sensors.power.total.value < _max}

    def _validate(self):
        if self.model.fuse_max == 0:
            return
        if self.hub.options.peaqev_lite:
            return
        if self.model.is_valid:
            self._enabled = True
            self._active = True

    """
    1 if trying to raise amps and they would hit mains-treshold, dont raise
    2 if approaching mains-threshold on current amps. Lower if possible, else turn off
    """
