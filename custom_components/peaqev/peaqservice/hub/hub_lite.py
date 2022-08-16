import logging
from datetime import datetime

from homeassistant.core import (
    HomeAssistant,
)
from homeassistant.helpers.event import async_track_state_change
from peaqevcore.hub.hub_options import HubOptions
from peaqevcore.services.threshold.threshold_lite import ThresholdLite

import custom_components.peaqev.peaqservice.util.extensionmethods as ex
from custom_components.peaqev.peaqservice.chargecontroller.chargecontroller_lite import ChargeControllerLite
from custom_components.peaqev.peaqservice.hub.hubbase import HubBase
from custom_components.peaqev.peaqservice.hub.hubdata.hubdata_lite import HubDataLite
from custom_components.peaqev.peaqservice.util.constants import CHARGERCONTROLLER

_LOGGER = logging.getLogger(__name__)

class HubLite(HubBase, HubDataLite):
    """This is the hub used for peaqev-lite. Without power meter readings"""
    def __init__(
        self,
        hass: HomeAssistant,
        options: HubOptions,
        domain: str,
        config_inputs: dict
        ):
        super().__init__(hass=hass, options=options, domain=domain)
        self.create_hub_data(self.hass, options, self.domain, config_inputs)

        self.threshold = ThresholdLite(self)
        self.chargecontroller = ChargeControllerLite(self)

        self.init_hub_values()

        trackerEntities = [
            self.chargerobject_switch.entity,
            self.totalhourlyenergy.entity
        ]

        self.chargingtracker_entities = [
            self.carpowersensor.entity,
            self.charger_enabled.entity,
            self.charger_done.entity,
            self.chargerobject.entity,
            f"sensor.{self.domain}_{ex.nametoid(CHARGERCONTROLLER)}",
        ]

        if self.hours.price_aware is True:
            if self.hours.nordpool_entity is not None:
                self.chargingtracker_entities.append(self.hours.nordpool_entity)

        trackerEntities += self.chargingtracker_entities

        async_track_state_change(hass, trackerEntities, self.state_changed)

    def is_initialized(self) -> bool:
        ret = [self.hours.is_initialized,
               self.carpowersensor.is_initialized,
               self.chargerobject_switch.is_initialized,
               self.chargerobject.is_initialized
               ]
        return all(ret)

    @property
    def current_peak_dynamic(self):
        if self.price_aware is True and len(self.dynamic_caution_hours):
            if datetime.now().hour in self.dynamic_caution_hours.keys() and self.timer.is_override is False:
                return self.current_peak.value * self.dynamic_caution_hours[datetime.now().hour]
        return self.current_peak.value

    async def _update_sensor(self, entity, value):
        match entity:
            case self.carpowersensor.entity:
                self.carpowersensor.value = value
            case self.chargerobject.entity:
                self.chargerobject.value = value
            case self.chargerobject_switch.entity:
                self.chargerobject_switch.value = value
                self.chargerobject_switch.updatecurrent()
            case self.current_peak.entity:
                self.current_peak.value = value
            case self.totalhourlyenergy.entity:
                self.totalhourlyenergy.value = value
                self.current_peak.value = self.locale.data.query_model.observed_peak
                self.locale.data.query_model.try_update(
                    new_val=float(value),
                    timestamp=datetime.now()
                )
            case self.hours.nordpool_entity:
                self.hours.update_nordpool()

        if entity in self.chargingtracker_entities:
            await self.charger.charge()
