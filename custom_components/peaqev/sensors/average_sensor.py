from datetime import timedelta

from homeassistant.components.filter.sensor import (
    OutlierFilter, LowPassFilter,
    TimeSMAFilter, SensorFilter,
    TIME_SMA_LAST
)

import custom_components.peaqev.peaqservice.util.extensionmethods as ex
from custom_components.peaqev.const import DOMAIN
from custom_components.peaqev.peaqservice.util.constants import POWERCONTROLS


class PeaqAverageSensor(SensorFilter):
    def __init__(self, hub, entry_id, name, filtertimedelta):
        self.hub = hub
        self._entry_id = entry_id
        self._attr_name = f"{hub.hubname} {name}"
        super().__init__(
            self._attr_name,
            self.unique_id,
            self.hub.sensors.power.house.entity,
            self._set_filters(self.hub, filtertimedelta)
        )

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.hub.hub_id, POWERCONTROLS)}}

    @property
    def unique_id(self):
        """Return a unique ID to use for this sensor."""
        return f"{DOMAIN}_{self._entry_id}_{ex.nametoid(self._attr_name)}"

    def _set_filters(self, hub, filtertimedelta: timedelta) -> list:
        filters = []

        filters.append(LowPassFilter(1, 0, hub.sensors.power.house.entity, 10))
        filters.append(TimeSMAFilter(filtertimedelta, 0, hub.sensors.power.house.entity, TIME_SMA_LAST))
        filters.append(OutlierFilter(4, 0, hub.sensors.power.house.entity, 2))

        return filters
