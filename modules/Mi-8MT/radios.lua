-- Radios read by dcs/instructor_export.lua for the agents' frequency gate, in SRS's order (radio 0 is the intercom,
-- so this list starts at radio 1). Devices, modulation and the selected radio as SRS's own Mi-8 exporter reads them
-- (Saved Games/DCS/Mods/Services/DCS-SRS/Scripts/DCS-SRS-Modules/Mi8.lua, read 2026-11-02): R-863 = device 38, AM/FM
-- by argument 369 (FM above 0.5); JADRO-1A = 37 (AM); R-828 = 39 (FM). The SPU-9 selector (argument 550, steps of
-- 0.1) picks the radio: 0 R-863, 1 JADRO-1A, 2 R-828, anything else none; the Radio/ICS switch (553) on = intercom.
-- PTT buttons 182 and 225. Added after a live flight in which the Mi-8 had no radio data, so every press went to the
-- instructor on 251.0 AM and the pilot, tuned elsewhere, never heard the answers.
return {
    radios = {
        {name = 'R-863', device = 38, modulation_arg = 369},
        {name = 'JADRO-1A', device = 37, modulation = 0},
        {name = 'R-828', device = 39, modulation = 1},
    },
    selector = {arg = 550, step = 0.1, positions = {[0] = 1, [1] = 2, [2] = 3}},
    intercom_arg = 553,
    ptt_args = {182, 225},
}
