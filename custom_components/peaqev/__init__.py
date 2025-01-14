"""The peaqev integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import \
    ConfigEntry  # pylint: disable=import-error
from homeassistant.core import HomeAssistant  # pylint: disable=import-error

from custom_components.peaqev.peaqservice.hub.hub import HomeAssistantHub
from custom_components.peaqev.peaqservice.hub.models.hub_options import \
    HubOptions
from custom_components.peaqev.peaqservice.util.constants import TYPELITE
from custom_components.peaqev.services import async_prepare_register_services

from .const import DOMAIN, PLATFORMS
from .peaqservice.chargertypes.models.chargertypes_enum import ChargerType

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, conf: ConfigEntry) -> bool:
    """Set up Peaqev"""

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][conf.entry_id] = conf.data
    options = await async_set_options(conf)
    hub = HomeAssistantHub(hass, options, DOMAIN)
    hass.data[DOMAIN]["hub"] = hub
    await hub.setup()

    conf.async_on_unload(conf.add_update_listener(async_update_entry))
    await async_prepare_register_services(hub, hass)

    for platform in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(conf, platform)
        )

    return True


PRICE_CHANGES = [
    "min_price",
    "top_price",
    "cautionhour_type",
    "dynamic_top_price",
    "blocknocturnal",
    "max_charge",
    "cautionhours",
    "_startpeaks",
    "nonhours",
]

RELOAD_CHANGES = ["fuse_type", "gain_loss", "price_aware"]


async def async_update_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Reload Peaqev component when options changed."""
    _LOGGER.debug("Reloading Peaqev component")
    new_options = await async_set_options(config_entry)
    old_options = hass.data[DOMAIN]["hub"].options
    diff = old_options.compare(new_options)
    hass.data[DOMAIN]["hub"].options = new_options
    if len(diff) == 0:
        return
    if [i for i in diff if i in RELOAD_CHANGES]:
        return await hass.config_entries.async_reload(config_entry.entry_id)
    if [i for i in diff if i in PRICE_CHANGES]:
        await hass.data[DOMAIN]["hub"].initializer.async_init_hours()


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)


async def async_set_options(conf) -> HubOptions:
    options = HubOptions()
    options.peaqev_lite = bool(conf.data.get("peaqevtype") == TYPELITE)
    if options.peaqev_lite is False:
        options.powersensor = conf.data["name"]
    options.locale = conf.data.get("locale", "")
    options.charger.chargertype = conf.data.get("chargertype", "")
    if options.charger.chargertype == ChargerType.Outlet.value:
        options.charger.powerswitch = conf.data.get("outletswitch", "")
        options.charger.powermeter = conf.data.get("outletpowermeter", "")
    elif options.charger.chargertype != ChargerType.NoCharger.value:
        options.charger.chargerid = conf.data.get("chargerid", "")
    if options.charger.chargertype == ChargerType.NoCharger.value:
        options.powersensor_includes_car = True
    else:
        options.powersensor_includes_car = conf.data.get(
            "powersensorincludescar", False
        )
    options.startpeaks = conf.options.get("startpeaks", conf.data.get("startpeaks"))
    options.cautionhours = await async_get_existing_param(conf, "cautionhours", [])
    options.nonhours = await async_get_existing_param(conf, "nonhours", [])
    options.price.price_aware = await async_get_existing_param(
        conf, "priceaware", False
    )
    options.price.min_price = await async_get_existing_param(
        conf, "min_priceaware_threshold_price", 0
    )
    options.price.top_price = await async_get_existing_param(
        conf, "absolute_top_price", 0
    )
    options.price.dynamic_top_price = await async_get_existing_param(
        conf, "dynamic_top_price", False
    )
    options.price.cautionhour_type = await async_get_existing_param(
        conf, "cautionhour_type", "intermediate"
    )
    options.max_charge = conf.options.get("max_charge", 0)
    options.fuse_type = await async_get_existing_param(conf, "mains", "")
    options.blocknocturnal = await async_get_existing_param(
        conf, "blocknocturnal", False
    )
    options.gainloss = await async_get_existing_param(conf, "gainloss", False)
    return options


async def async_get_existing_param(conf, parameter: str, default_val: any):
    return conf.options.get(parameter, conf.data.get(parameter, default_val))
