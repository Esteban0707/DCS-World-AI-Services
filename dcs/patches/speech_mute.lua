-- Silence DCS's own ATC voice and the calls to ATC: DCS_AI_Service's ATC answers on the radio instead. Subtitles and
-- the radio events are kept, so DCS's own ATC keeps working as the traffic authority (it still holds and releases AI
-- traffic). Adapted from DCS-ATC's speech patch (verified live 2026-10-25).
-- Flights and wingmen (user, 2026-11-05: the AI wingman's own DCS voice on its frequency, "airborne", "on station",
-- "in", over our wingman agent): DCS's flight reports (wMsgFlight range), the leader's calls to its wingmen
-- (wMsgLeader) and the wingmen's replies and reports (wMsgWingmen) are always silenced: our wingman agents talk
-- instead. Before, the last two waited for our F1 bridge's first wingman order (DCSAIServiceBridge.wingmen_ordered),
-- which the scripted wingman never gives.
-- DCS's AWACS (user, 2026-11-08: "callar el awacs de dcs"): its calls (wMsgAWACS range) and the calls to it
-- (wMsgLeaderToAWACS) are silenced too; our air controller (controller/desk.py, e.g. Magic) talks instead.
if result then
	local event, M = message.event, base.Message
	if (event > M.wMsgATCNull and event < M.wMsgATCMaximum) or
		(event > M.wMsgLeaderToATCNull and event < M.wMsgLeaderToATCMaximum) or
		(event > M.wMsgAWACSNull and event < M.wMsgAWACSMaximum) or
		(event > M.wMsgLeaderToAWACSNull and event < M.wMsgLeaderToAWACSMaximum) or
		(event > M.wMsgFlightNull and event < M.wMsgFlightMaximum) or
		(event > M.wMsgLeaderNull and event < M.wMsgLeaderMaximum) or
		(event > M.wMsgWingmenNull and event < M.wMsgWingmenMaximum) then
		result.files = {}
	end
end
