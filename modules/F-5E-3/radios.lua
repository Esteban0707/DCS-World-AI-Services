-- Radios read by dcs/instructor_export.lua for the instructor frequency gate, in SRS's order (radio 0 is the
-- intercom, so this list starts at radio 1). "device" is the DCS cockpit device of the radio, as SRS's own F-5E
-- exporter reads it (Saved Games/DCS/Mods/Services/DCS-SRS/Scripts/DCS-SRS-Modules/F5E.lua, SRS 2.4.1.0:
-- SR.getRadioFrequency(23)). The F-5E has one radio; SRS always selects it.
return {
    selected = 1,
    radios = {
        {name = 'AN/ARC-164', device = 23},
    },
}
