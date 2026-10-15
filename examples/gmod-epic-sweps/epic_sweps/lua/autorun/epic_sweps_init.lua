-- Epic SWEPs: 100 ZOMBIE SPAWNER + 7000 F*CKING MISSILES
--
-- Loaded on both realms from lua/autorun. GMod has no guaranteed load order in
-- there, so we include our own modules in a fixed order instead of hoping the
-- filenames sort themselves out.
--
--   SERVER: the timetables, the horde, the damage, and the broadcast.
--   CLIENT: the show (lua/epic_sweps/client/*) and the big text.

local BASE = "epic_sweps/"

EpicSWEPs = EpicSWEPs or {}
EpicSWEPs.Version = "1.0"

-- load order matters: config first, then helpers, then features
local SHARED = {
	"shared/epic_sweps_config.lua",
	"shared/epic_sweps_util.lua",
	"shared/epic_sweps_particles.lua",
	"shared/epic_sweps_net.lua",
	"shared/epic_sweps_timeline.lua",
	"shared/epic_sweps_zombies.lua",
	"shared/epic_sweps_cvars.lua"
}

local CLIENT = {
	"client/epic_sweps_hud.lua",
	"client/epic_sweps_render.lua"
}

if SERVER then
	for _, path in ipairs(SHARED) do
		AddCSLuaFile(BASE .. path)
	end

	for _, path in ipairs(CLIENT) do
		AddCSLuaFile(BASE .. path)
	end
end

for _, path in ipairs(SHARED) do
	include(BASE .. path)
end

if CLIENT then
	for _, path in ipairs(CLIENT) do
		include(BASE .. path)
	end
end

-- The two entity tables the SWEPs talk to. The entity files fill them in when the
-- entity scripts load, which happens after this - the tables only need to exist.
EpicSWEPs.Zombies = EpicSWEPs.Zombies or {}
EpicSWEPs.Missiles = EpicSWEPs.Missiles or {}

-- Particles we borrow are not all loaded by default; InitPostEntity does the
-- actual call, this only makes sure the module is awake.
