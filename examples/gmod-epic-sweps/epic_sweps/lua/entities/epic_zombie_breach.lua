--[[ epic_zombie_breach - a box that should not be this dramatic.

	Server side: the timetable and the horde. The visuals are driven from the
	state we broadcast (see lua/epic_sweps/client/epic_sweps_render.lua), so a
	hidden or clipped entity still gets the full show. ]]

EpicSWEPs = EpicSWEPs or {}
EpicSWEPs.Zombies = EpicSWEPs.Zombies or {}

ENT.Type = "anim"
ENT.Base = "base_anim"

ENT.PrintName = "100 Zombie Breach"
ENT.Author = "universal-modder"
ENT.Purpose = "The breach that spits out a hundred zombies."
ENT.Spawnable = false
ENT.AdminOnly = false
ENT.RenderGroup = RENDERGROUP_NONE

local C = EpicSWEPs.Config
local Z = C.ZombieSpawner

-- Spawn a breach. Callable from either realm; a no-op on the client, so SWEPs and
-- console commands can both call it without checking where they are.
function EpicSWEPs.Zombies.SpawnBreach(pos, owner, count)
	if not SERVER then return NULL end

	local ent = ents.Create("epic_zombie_breach")
	if not IsValid(ent) then return NULL end

	ent:SetPos(pos)

	if IsValid(owner) then
		ent.EpicOwner = owner
	end

	if count then
		ent:SetVar("EpicZombieCount", math.Clamp(count, 1, 1000))
	end

	ent:Spawn()

	return ent
end

local function SeedFrom(pos)
	return math.floor(math.abs(pos.x * 17 + pos.y * 31 + 977)) % 65536
end

function ENT:Initialize()
	self.Seed = SeedFrom(self:GetPos())

	if not SERVER then return end

	self:SetModel("models/props_junk/cardboard_box004a.mdl")
	self:PhysicsInit(SOLID_VPHYSICS)
	self:SetMoveType(MOVETYPE_NONE)
	self:SetSolid(SOLID_NONE)
	self:SetNoDraw(true)
	self:DrawShadow(false)

	self.Count = math.Clamp(self:GetVar("EpicZombieCount", Z.ZombieCount or 100), 1, 1000)
	self.Radius = Z.HordeRadius or 620

	local instant = EpicSWEPs.Util.CVarValue("epic_zombies_instant", 0) > 0
	self.Timeline = EpicSWEPs.Timeline.Breach(CurTime(), self.Count,
		instant and 1 or (Z.Waves or 4), Z.WaveDelay or 0.45)

	self.Spawned = 0
	self.NextWave = 0
	self.NextSync = 0
	self.StartedEffects = false
	self.OpenedPortal = false
end

function ENT:Announce(text, style, duration)
	EpicSWEPs.Net.Announce(true, { text = text, style = style, duration = duration })
end

function ENT:PlayVoice(index)
	if EpicSWEPs.Util.CVarValue("epic_play_voice", 1) == 0 then return end

	local v = C.Voice.ZombieSpawner[index]
	if not v then return end

	EpicSWEPs.Util.PlaySound(self, v.file, v.vol or 1)
	self:Announce(v.text, index == 1 and "subtitle" or "alert", 2.2)
end

function ENT:Sync(force)
	local now = CurTime()
	if not force and now - self.NextSync < 0.2 then return end
	self.NextSync = now

	local tl = self.Timeline

	EpicSWEPs.Net.SendState("breach", {
		e = self:EntIndex(),
		p = self:GetPos(),
		s = tl.start,
		c = self.Count,
		r = self.Radius,
		ph = self.Phase or 0,
		sd = self.Seed,
		sp = self.Spawned
	}, self)
end

function ENT:StartEffects()
	if self.StartedEffects then return end
	self.StartedEffects = true

	self:Announce((C.Effects.Title and C.Effects.Title.ZombieSpawner) or "100 ZOMBIE SPAWNER", "title", 2.2)
	self:PlayVoice(1)

	local S = C.Sounds.ZombieSpawner
	EpicSWEPs.Util.PlaySound(self, EpicSWEPs.Util.PickSound(S.Open), 1)
	EpicSWEPs.Util.PlaySound(self, S.Hum, 0.6, EpicSWEPs.Util.LVL.IDLE)

	self:Sync(true)
end

function ENT:Think()
	local tl = self.Timeline
	local t = CurTime() - tl.start

	self:StartEffects()

	-- the box lets go
	if not self.OpenedPortal and t >= tl.second then
		self.OpenedPortal = true
		self.Phase = 1

		self:Announce("THE BOX IS OPEN", "alert", 1.6)
		self:PlayVoice(2)

		EpicSWEPs.Util.PlaySound(self, EpicSWEPs.Util.PickSound(C.Sounds.ZombieSpawner.Open), 1)

		local e = EffectData()
		e:SetOrigin(self:GetPos())
		e:SetScale(1.6)
		e:SetMagnitude(self.Seed % 1023)	-- EffectData magnitudes are networked as 0-1023
		util.Effect("epic_zombie_burst", e, true, true)

		self:Sync(true)
	end

	-- the horde arrives
	if t >= tl.release and self.Spawned < self.Count then
		self.Phase = 2
		self:SpawnWave()
	end

	if self.Spawned >= self.Count and not self.Done then
		self.Phase = 3
		self.Done = true
		self:Finish()
		return true
	end

	self:Sync()

	if self.Phase == 2 then
		self:NextThink(CurTime() + 0.05)
	else
		self:NextThink(CurTime() + 0.25)
	end

	return true
end

function ENT:SpawnWave()
	local tl = self.Timeline
	local now = CurTime()
	if now < self.NextWave then return end

	local perWave = math.ceil(self.Count / math.max(tl.waves, 1))
	local waveIndex = math.floor((now - tl.release) / math.max(tl.waveDelay, 0.01))
	local ringRadius = self.Radius * (0.35 + 0.65 * (waveIndex / math.max(tl.waves, 1)))
	local last = math.min(self.Spawned + perWave, self.Count)

	for i = self.Spawned + 1, last do
		if not EpicSWEPs.Zombies.CanSpawn() then
			self:Announce("ENTITY LIMIT REACHED - " .. self.Spawned .. " GOT THROUGH", "subtitle", 2)
			self.Spawned = self.Count
			break
		end

		self:SpawnOne(i, self.Count, ringRadius)
	end

	self.NextWave = now + math.max(tl.waveDelay, 0.01)

	local e = EffectData()
	e:SetOrigin(self:GetPos())
	e:SetScale(ringRadius / 100)
	e:SetMagnitude(self.Seed % 1023)	-- EffectData magnitudes are networked as 0-1023
	util.Effect("epic_zombie_wave", e, true, true)
end

function ENT:SpawnOne(index, count, radius)
	local pool = EpicSWEPs.Zombies.GetModelPool()
	if not pool then
		self.Spawned = count	-- no zombie models at all: do not loop forever
		return
	end

	local ang = (index / count) * 360 + math.Rand(-5, 5)
	local r = radius * math.Rand(0.7, 1.15)
	local origin = self:GetPos() + Vector(math.cos(ang) * r, math.sin(ang) * r, 0)
	local pos = EpicSWEPs.Zombies.GroundPos(origin)
	local model = pool[((index - 1) % #pool) + 1]
	local face = Angle(0, -ang + 90, 0)
	local ent

	if Z.AllowNPCs ~= false then
		ent = EpicSWEPs.Zombies.SpawnNPC(pos + Vector(0, 0, 40), face, model)
	end

	if not IsValid(ent) and Z.AllowRagdolls ~= false then
		ent = EpicSWEPs.Zombies.SpawnRagdoll(pos + Vector(0, 0, 40), face, model)
	end

	-- counted either way: a failed spawn must not stall the waves
	self.Spawned = self.Spawned + 1

	if not IsValid(ent) then return end

	ent.EpicBreach = self

	if ent:IsNPC() then
		-- sandbox's NPC think picks targets; this just starts them hunting now
		pcall(function() ent:SetSchedule(SCHED_CHASE_ENEMY) end)
	end

	EpicSWEPs.Zombies.Vocalise(ent)

	local e = EffectData()
	e:SetOrigin(ent:GetPos())
	e:SetScale(1)
	e:SetMagnitude(self.Seed % 1023)	-- EffectData magnitudes are networked as 0-1023
	util.Effect("epic_zombie_pop", e, true, true)
end

function ENT:Finish()
	if self.Finished then return end
	self.Finished = true

	self:Announce(self.Spawned .. " ZOMBIES LOOSE", "subtitle", 2.2)

	local e = EffectData()
	e:SetOrigin(self:GetPos())
	e:SetScale(3)
	e:SetMagnitude(self.Seed % 1023)	-- EffectData magnitudes are networked as 0-1023
	util.Effect("epic_zombie_burst", e, true, true)

	self:Sync(true)

	timer.Simple(C.Seq.RemoveDelay or 1.5, function()
		if IsValid(self) then self:Remove() end
	end)
end

function ENT:OnRemove()
	EpicSWEPs.Net.SendState("breach", { e = self:EntIndex(), dead = true }, self)
end

function ENT:Use(activator) end
