--[[ Particle / effect garnish.

	Deliberately built out of GMod's *default engine effects* (VortDispel, ThumperDust,
	ManhackSparks, StunstickImpact) instead of named .pcf particle systems: those
	exist in every install, need no precaching and no Particle Editor 2, and they
	cost nothing to skip if the name is ever missing on someone's game.

	Everything here is CLIENT-side and decorative: a failure must never break the
	weapon, so each call is guarded and cheap. ]]

EpicSWEPs = EpicSWEPs or {}

local C = EpicSWEPs.Config
local P = C.Particles or {}

local M = {}

-- util.Effect from the client draws locally only - exactly what garnish wants.
function M.Engine(name, pos, scale, ang)
	if not CLIENT or not name or name == "" then return false end

	local d = EffectData()
	d:SetOrigin(pos)
	if scale then d:SetScale(scale) end
	if ang then d:SetAngles(ang) end

	return pcall(util.Effect, name, d, true)
end

-- Optional: a real particle system, only if the user configured one that is
-- precached (see game.AddParticles / PrecacheParticleSystem).
function M.Play(pos, ang)
	local name = P.ParticleSystem
	if not CLIENT or not name or name == "" then return false end

	return pcall(ParticleEffect, name, pos, ang or angle_zero)
end

-- A ring of dust where the breach opens.
function M.Ring(centre, radius, points)
	local scale = math.Clamp((radius or 200) / 64, 0.5, 12)

	for i = 1, math.Clamp(points or 6, 1, 8) do
		local a = (i / (points or 6)) * 360
		M.Engine(P.Dust, centre + Vector(math.cos(a), math.sin(a), 0) * (radius or 200), scale)
	end
end

-- The big green pulse that rolls out along the floor.
function M.Pillar(centre, height, points)
	M.Engine(P.Breach, centre)
	M.Engine(P.Dust, centre, 6)

	for i = 1, math.Clamp(points or 3, 1, 6) do
		M.Engine(P.Sparks, centre + Vector(0, 0, (i / (points or 3)) * (height or 400)), 1)
	end
end

-- One arriving zombie.
function M.ZombiePop(pos)
	M.Engine(P.Pop, pos + Vector(0, 0, 16), 1)
	M.Play(pos)
end

-- One crater: sparks up, dust out.
function M.ImpactPuff(pos)
	M.Engine(P.Sparks, pos + Vector(0, 0, 12), 1)
	M.Engine(P.Dust, pos, 2.5)
end

-- The laser dot on the painted target.
function M.LockMarker(pos)
	M.Engine("CommandPointer", pos, 1)
end

-- Only needed for the optional ParticleSystem above.
function M.LoadClientEmitters()
	if not CLIENT then return end

	for cvar, val in pairs(C.ParticleClientCommands or {}) do
		local c = GetConVar(cvar)
		if c and c:GetInt() == 0 then
			RunConsoleCommand(cvar, val)
		end
	end
end

EpicSWEPs.Particles = M
return M
