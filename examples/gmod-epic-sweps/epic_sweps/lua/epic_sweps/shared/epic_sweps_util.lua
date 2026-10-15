--[[ Shared helpers: sound, deterministic RNG, screen math, shake.

	Sound goes out with EmitSound so it works for both Valve's own sounds and our
	sound script entries without needing PrecacheScriptSound anywhere. ]]

EpicSWEPs = EpicSWEPs or {}

local C = EpicSWEPs.Config
local M = {}

local loops = {}

-- ---------------------------------------------------------------- sound ----
-- Entity:EmitSound( name, soundLevel, pitchPercent, volume, channel ) - that order.
-- Sound channels and levels, as numbers. Two reasons these are ours and not the engine's names:
-- GMod does not expose the engine's SNDLVL_* enum to Lua at all (the wiki's own advice is "use the raw
-- number values"), and the channel constants are easy to write as the non-existent SNDCHAN_* - which is
-- not an error, just nil in the argument, so the sound quietly plays wrong or not at all.
-- Values from the wiki's Enums/CHAN and Enums/SNDLVL pages.
M.CHAN = { AUTO = 0, WEAPON = 1, VOICE = 2, STATIC = 6 }
M.LVL = { IDLE = 60, STATIC = 66, NORM = 75, TALKING = 80, LOUD = 100, GUNFIRE = 140 }

function M.PlaySound(ent, soundName, volume, level, pitch)
	if not soundName or soundName == "" then return end

	local e = IsValid(ent) and ent or (CLIENT and LocalPlayer() or nil)
	if not IsValid(e) then return end

	-- order matters here: name, soundLevel, pitch, volume, channel
	e:EmitSound(soundName, level or M.LVL.GUNFIRE, pitch or math.random(94, 106), volume or 1, M.CHAN.AUTO)
end

-- A sound at a world position: the global EmitSound has a different order again,
-- ( name, position, entity, channel, volume, soundLevel, flags, pitch ).
function M.PlayAt(pos, soundName, volume, level, pitch)
	if not CLIENT or not soundName or soundName == "" then return end

	-- the *global* EmitSound, whose order is different again: name, pos, entity, channel, volume, level, flags, pitch
	EmitSound(soundName, pos, 0, M.CHAN.AUTO, volume or 1, level or M.LVL.GUNFIRE, 0, pitch or math.random(94, 106))
end

-- Pick a random entry out of a table of sounds (or accept a bare string).
function M.PickSound(t)
	if not t then return nil end
	if isstring(t) then return t end
	if not istable(t) then return nil end
	return t[math.random(1, #t)]
end

-- Looping sound, one channel per key so starting twice does not stack.
function M.PlayLoop(ent, key, soundName, volume, pitch)
	if not CLIENT then return end

	local target = IsValid(ent) and ent or game.GetWorld()
	if not target then return end

	M.StopLoop(key)
	if not soundName then return end

	local ch = loops[key]
	if not ch then
		ch = CreateSound(target, soundName)
		loops[key] = ch
	end

	ch:PlayEx(volume or 1, pitch or 100)
end

function M.StopLoop(key)
	if not CLIENT then return end

	local ch = loops[key]
	if ch then
		ch:Stop()
		loops[key] = nil
	end
end

function M.StopAllLoops()
	if not CLIENT then return end

	for k, ch in pairs(loops) do
		ch:Stop()
		loops[k] = nil
	end
end

-- ------------------------------------------------------- deterministic rng --
-- Server and client must agree on where 7000 missiles land, so instead of
-- networking 7000 positions we reseed the same rng on both sides.
function M.SeededRandom(seed)
	math.randomseed(math.floor((seed or 0) % 2147483647))
end

-- The one authoritative impact computation. Server and client each run this once
-- per barrage, with the same seed, and come out with the same numbers: the list to
-- draw and the subset that does damage. A single pass keeps them in lockstep.
function M.Impacts(centre, radius, seed, count, want)
	-- Both realms call this with numbers that came off the wire, so nothing here may assume a complete
	-- table. An arithmetic error inside the renderer repeats every frame and GMod eventually silences the
	-- hook, which is the worst possible failure for a weapon that is supposed to be the fun part.
	if not centre then return {}, {} end
	count = math.max(0, math.floor(count or 0))
	if count == 0 then return {}, {} end
	radius = radius or 0
	if radius <= 0 then radius = 1 end

	local b = C.MissileBarrage or {}
	local vis = b.Visuals or {}
	local dmgTotal = M.DamageMissileCount(count)

	want = math.Clamp(want or 1200, 1, count)

	local step = count / want
	local lastBucket = -1
	local list = {}
	local dmg = {}

	M.SeededRandom(seed)

	local function make(i, ang, r, h, fall, tail, width)
		return {
			index = i,
			pos = centre + Vector(math.cos(ang) * r, math.sin(ang) * r, math.Rand(-30, 30)),
			t = (i - 1) / math.max(count - 1, 1),
			fall = fall,
			height = h,
			tail = tail,
			width = width
		}
	end

	for i = 1, count do
		local ang = math.Rand(0, 360)
		local r = radius * math.sqrt(math.Rand(0, 1))
		local h = math.Rand(64, 900)
		local fall = (vis.FallTime or 0.9) * math.Rand(0.85, 1.15)
		local tail = math.Rand(vis.MinTail or 140, vis.MaxTail or 320)
		local width = (vis.Width or 15) * math.Rand(0.7, 1.3)

		local entry

		if i <= dmgTotal then
			entry = make(i, ang, r, h, fall, tail, width)
			dmg[#dmg + 1] = entry
		end

		local bucket = math.floor((i - 1) / step)
		if bucket ~= lastBucket or i == count then
			lastBucket = bucket
			entry = entry or make(i, ang, r, h, fall, tail, width)
			entry.draw = true
			entry.dmg = i <= dmgTotal
			list[#list + 1] = entry
		end
	end

	return list, dmg
end

-- How many impacts also do real damage. Both realms call this so they line up.
function M.DamageMissileCount(total)
	local b = C.MissileBarrage
	local pct = b and b.DamageMissilePercent or 7
	return math.Clamp(math.floor((total or 0) * (pct / 100)), 1, total or 1)
end

-- Sample the whole barrage down to something a frame can hold: an evenly spread
-- list of impact descriptors, computed once per barrage instead of per missile
-- per frame. Same seed on both realms, so it still matches the damage.

-- ------------------------------------------------------------- screen math --
-- Screen-space distance/position of a world point: {x, y, visible}.
function M.ToScreen(pos)
	return pos:ToScreen()
end

-- Screen radius of a world circle around pos, from the local view.
function M.ScreenRadius(pos, worldRadius)
	local eye = EyePos()
	local flat = Vector(pos.x, pos.y, eye.z)
	local dir = eye - flat

	if dir:LengthSqr() < 1 then
		dir = Vector(1, 0, 0)
	else
		dir:Normalize()
	end

	local a = pos:ToScreen()
	local b = (pos + dir * worldRadius):ToScreen()

	if not a.visible or not b.visible then return 0 end

	return Vector(a.x - b.x, a.y - b.y, 0):Length()
end

-- ------------------------------------------------------------------ shake ---
function M.ShakeAt(pos, amplitude, frequency, duration, radius)
	if not SERVER then return end
	if M.CVarValue("epic_screen_shake", 1) == 0 then return end

	util.ScreenShake(pos, amplitude, frequency, duration, radius)
end

-- ------------------------------------------------------------------- cvars --
function M.CVarValue(name, fallback)
	local c = EpicSWEPs.CVars and EpicSWEPs.CVars[name]
	if not c then return fallback end
	return c:GetInt()
end

EpicSWEPs.Util = M
return M
