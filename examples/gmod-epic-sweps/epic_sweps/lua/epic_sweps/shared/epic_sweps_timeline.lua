local C = EpicSWEPs.Config
local M = {}

local function get(t, k, fallback)
	if t and t[k] ~= nil then return t[k] end
	return fallback
end

-- The missile barrage. centre = where it lands, start = CurTime() at spawn.
function M.Barrage(centre, start, count)
	local b = C.MissileBarrage
	local seq = C.Seq or {}
	local spread = get(b, "ImpactSpreadTime", 2.6)
	local impacts = get(seq, "Impacts", 3.35)

	count = count or b.MissileCount or 7000

	local v1 = C.Voice.MissileBarrage[1]
	local v2 = C.Voice.MissileBarrage[2]

	return {
		centre = centre,
		start = start,
		count = count,
		damageCount = EpicSWEPs.Util.DamageMissileCount(count),
		radius = get(b, "ImpactRadius", 1400),
		spread = spread,
		announce = get(seq, "Announce", 0),
		voice1 = get(v1, "start", 0.25),
		voice2 = get(v2, "start", get(seq, "Voice2", 1.6)),
		lock = get(seq, "Lock", 3.0),
		impacts = impacts,
		endTime = math.max(get(seq, "End", 8.5), impacts + spread),
		removeDelay = get(seq, "RemoveDelay", 1.5)
	}
end

-- The zombie breach. Same idea, much angrier timetable.
function M.Breach(start, count, waves, waveDelay)
	local z = C.ZombieSpawner
	local seq = C.Seq or {}

	count = count or z.ZombieCount or 100
	waves = waves or z.Waves or 4
	waveDelay = waveDelay or z.WaveDelay or 0.45

	local finish = (waves - 1) * waveDelay

	return {
		start = start,
		count = count,
		waves = waves,
		waveDelay = waveDelay,
		open = get(seq, "Announce", 0),
		second = get(seq, "Voice2", 1.6),
		release = get(seq, "Lock", 3.0),
		finish = finish,
		endTime = finish + get(seq, "Impacts", 3.35) + 2.0,
		removeDelay = get(seq, "RemoveDelay", 1.5)
	}
end

EpicSWEPs.Timeline = M
return M
