--[[ The four effects both weapons fire through util.Effect. Everything here is
	drawn with the render API on purpose: no client-side entities, so 7000 impacts
	do not create 7000 anything. Particles come from EpicSWEPs.Particles, which
	silently does nothing if an emitter is unavailable. ]]

local C = EpicSWEPs and EpicSWEPs.Config or {}
local P = C.Particles or {}
local PT = EpicSWEPs.Particles
local U = EpicSWEPs.Util

local FLASH = Material("epic_sweps/flash")
local RING = Material("effects/select_ring")

-- A light you can see from the other side of the map.
local function light(pos, r, g, b, brightness, size, life)
	local dl = DynamicLight(math.random(1, 65535))
	if not dl then return end

	dl.pos = pos
	dl.r, dl.g, dl.b = r, g, b
	dl.brightness = brightness
	dl.Decay = 1600
	dl.Size = size
	dl.DieTime = CurTime() + life
end

-- ========================================================================
-- epic_explosion: the big one, under every missile that bites
-- ========================================================================
local EFFECT = {}

function EFFECT:Init(data)
	self.Origin = data:GetOrigin()
	self.Scale = math.max(data:GetScale(), 0.4)
	self.StartTime = CurTime()
	self.Life = 0
	self.Die = CurTime() + 0.6

	local d = (self.Origin - EyePos()):Length()

	PT.ImpactPuff(self.Origin)

	light(self.Origin + Vector(0, 0, 40), 255, 190, 110, math.Clamp(6 - d / 900, 1, 6), 260 * self.Scale, 0.45)

	-- the boom is local to the listener: only nearby craters make noise
	if d < 3000 then
		local snd = (C.Sounds.MissileBarrage or {}).Explosion
		if snd then
			EpicSWEPs.Util.PlayAt(self.Origin, snd, math.Clamp(1 - d / 3200, 0.12, 1),
				SNDLVL_GUNFIRE, math.random(92, 108))
		end
	end
end

function EFFECT:Think()
	self.Life = (CurTime() - self.StartTime) / (self.Die - self.StartTime)
	return self.Life < 1
end

function EFFECT:Render()
	local t = self.Life
	if t < 0 or t >= 1 then return end

	local alpha = 1 - t

	-- fireball
	local size = 150 * self.Scale * (0.6 + t * 3.2)
	render.SetMaterial(FLASH)
	render.SuppressEngineLighting(true)
	render.DrawQuadEasy(self.Origin + Vector(0, 0, 20 + t * 60), Vector(0, 0, 1),
		size, size, Color(255, 200 - t * 90, 120 - t * 90, 230 * alpha), t * 120)

	-- ground shockwave
	if t < 0.55 then
		local r = 380 * self.Scale * (0.25 + t * 2.4)
		render.DrawQuadEasy(self.Origin + Vector(0, 0, 8), Vector(0, 0, 1), r, r,
			Color(255, 170, 80, 200 * (1 - t / 0.55)), t * 90)
	end

	render.SuppressEngineLighting(false)
end

Effects.RegisterEffect("epic_explosion", EFFECT)

-- ========================================================================
-- epic_zombie_burst: the box lets go
-- ========================================================================
local ZOMBIE = {}

function ZOMBIE:Init(data)
	self.Origin = data:GetOrigin()
	self.Scale = math.max(data:GetScale(), 0.4)
	self.StartTime = CurTime()
	self.Life = 0
	self.Die = CurTime() + 1.1

	PT.Ring(self.Origin, 170 * self.Scale, 6)
	PT.Pillar(self.Origin, 520, 4)

	light(self.Origin + Vector(0, 0, 60), 70, 255, 100, 5, 420, 0.8)
end

function ZOMBIE:Think()
	self.Life = (CurTime() - self.StartTime) / (self.Die - self.StartTime)
	return self.Life < 1
end

function ZOMBIE:Render()
	local t = self.Life
	local alpha = 1 - t
	if alpha <= 0 then return end

	render.SetMaterial(RING)
	render.SuppressEngineLighting(true)

	-- three rings flying outward, a beam, and a flash to sell it
	for i = 0, 2 do
		local r = (150 + i * 130) * self.Scale * (0.35 + t * 2.4)
		render.DrawQuadEasy(self.Origin + Vector(0, 0, 8 + i * 12), Vector(0, 0, 1),
			r * 2, r * 1.7, Color(70, 255, 100, 210 * alpha * (1 - i * 0.26)), -t * 140 + i * 30)
	end

	render.DrawBeam(self.Origin + Vector(0, 0, 10), self.Origin + Vector(0, 0, 760 * (1 - t * 0.6)),
		110 * (1 - t) + 24, 0, t * 6, Color(110, 255, 130, 190 * alpha))

	render.SuppressEngineLighting(false)

	render.SetMaterial(FLASH)
	render.SuppressEngineLighting(true)
	render.DrawQuadEasy(self.Origin + Vector(0, 0, 26), Vector(0, 0, 1),
		520 * self.Scale * (0.5 + t), 520 * self.Scale * (0.5 + t),
		Color(140, 255, 150, 130 * alpha), t * 60)
	render.SuppressEngineLighting(false)
end

Effects.RegisterEffect("epic_zombie_burst", ZOMBIE)

-- ========================================================================
-- epic_zombie_wave: one wave of the horde stepping through
-- ========================================================================
local WAVE = {}

function WAVE:Init(data)
	self.Origin = data:GetOrigin()
	self.Radius = math.max(data:GetScale() * 100, 100)
	self.StartTime = CurTime()
	self.Life = 0
	self.Die = CurTime() + 0.8

	PT.Ring(self.Origin, self.Radius, 8)
end

function WAVE:Think()
	self.Life = (CurTime() - self.StartTime) / (self.Die - self.StartTime)
	return self.Life < 1
end

function WAVE:Render()
	local t = self.Life
	local alpha = 180 * (1 - t)
	if alpha <= 4 then return end

	local r = self.Radius * (0.85 + t * 0.45)

	render.SetMaterial(RING)
	render.SuppressEngineLighting(true)
	render.DrawQuadEasy(self.Origin + Vector(0, 0, 6), Vector(0, 0, 1), r * 2, r * 1.7,
		Color(70, 255, 100, alpha), -t * 100)
	render.SuppressEngineLighting(false)
end

Effects.RegisterEffect("epic_zombie_wave", WAVE)

-- ========================================================================
-- epic_zombie_pop: one zombie, popping into existance
-- ========================================================================
local POP = {}

function POP:Init(data)
	self.Origin = data:GetOrigin()
	self.StartTime = CurTime()
	self.Life = 0
	self.Die = CurTime() + 0.5

	-- the actual "beamed in" puff, on the client, from a networked effect
	PT.ZombiePop(self.Origin)
end

function POP:Think()
	self.Life = (CurTime() - self.StartTime) / (self.Die - self.StartTime)
	return self.Life < 1
end

function POP:Render()
	local alpha = 1 - self.Life
	if alpha <= 0 then return end

	render.SetMaterial(FLASH)
	render.SuppressEngineLighting(true)
	render.DrawQuadEasy(self.Origin, Vector(0, 0, 1), 130 * (0.6 + self.Life), 130 * (0.6 + self.Life),
		Color(90, 255, 110, 150 * alpha), self.Life * 90)
	render.SuppressEngineLighting(false)
end

Effects.RegisterEffect("epic_zombie_pop", POP)
