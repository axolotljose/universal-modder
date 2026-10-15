--[[ epic_missile_barrage - seven thousand of them, on one position.

	One entity for the whole server. The streaks are drawn from a deterministic
	function on every client (never 7000 entities, never 7000 net messages); only
	the impacts that actually hurt anything touch the server at all.

	The big text is always drawn on screen; the voice line is a bonus. If the wav
	is missing the text still tells you what is happening. ]]

EpicSWEPs = EpicSWEPs or {}
EpicSWEPs.Missiles = EpicSWEPs.Missiles or {}

ENT.Type = "anim"
ENT.Base = "base_anim"

ENT.PrintName = "7000 Missiles"
ENT.Author = "universal-modder"
ENT.Purpose = "Blow up this man."
ENT.Spawnable = false
ENT.AdminOnly = false
ENT.RenderGroup = RENDERGROUP_NONE

local C = EpicSWEPs.Config
local B = C.MissileBarrage

function EpicSWEPs.Missiles.SpawnBarrage(pos, owner, count)
	if not SERVER then return NULL end

	local ent = ents.Create("epic_missile_barrage")
	if not IsValid(ent) then return NULL end

	ent:SetPos(pos)

	if IsValid(owner) then
		ent.EpicOwner = owner
	end

	if count then
		ent:SetVar("EpicMissileCount", math.Clamp(count, 1, 20000))
	end

	ent:Spawn()

	return ent
end

local function SeedFrom(pos, extra)
	return math.floor(math.abs(pos.x * 13 + pos.y * 29 + (extra or 1) * 7 + 1013)) % 65536
end

function ENT:Initialize()
	self.Seed = SeedFrom(self:GetPos(), 1)

	if not SERVER then return end

	self:SetMoveType(MOVETYPE_NONE)
	self:SetSolid(SOLID_NONE)
	self:SetNoDraw(true)
	self:DrawShadow(false)

	self.Count = math.Clamp(self:GetVar("EpicMissileCount", B.MissileCount or 7000), 1, 20000)
	self.Timeline = EpicSWEPs.Timeline.Barrage(self:GetPos(), CurTime(), self.Count)

	-- same call the clients make, so the fire lands where you can see it coming
	local _, dmgList = EpicSWEPs.Util.Impacts(self:GetPos(), self.Timeline.radius, self.Seed, self.Count, 64)
	self.DamageImpacts = dmgList

	self.Phase = 0
	self.Sent = 0
	self.NextSync = 0
	self.DidTitle = false
	self.DidVoice1 = false
	self.DidVoice2 = false
	self.DidLock = false
	self.DidImpact = false
	self.DidEnd = false
end

function ENT:Announce(text, style, duration)
	EpicSWEPs.Net.Announce(true, { text = text, style = style, duration = duration })
end

function ENT:PlayVoice(index)
	if EpicSWEPs.Util.CVarValue("epic_play_voice", 1) == 0 then return end

	local v = C.Voice.MissileBarrage[index]
	if v then
		EpicSWEPs.Util.PlaySound(self, v.file, v.vol or 1)
	end
end

function ENT:Sync(force)
	local now = CurTime()
	if not force and now - self.NextSync < 0.2 then return end
	self.NextSync = now

	local tl = self.Timeline

	EpicSWEPs.Net.SendState("barrage", {
		e = self:EntIndex(),
		p = self:GetPos(),
		s = tl.start,
		c = self.Count,
		r = tl.radius,
		sd = self.Seed,
		ph = self.Phase
	}, self)
end

function ENT:Think()
	local tl = self.Timeline
	local t = CurTime() - tl.start

	-- 1. the title, and the first line
	if not self.DidTitle then
		self.DidTitle = true
		self:Announce((C.Effects.Title and C.Effects.Title.MissileBarrage) or "7000 MISSILES", "title", 2.4)
		self:Sync(true)
	end

	if not self.DidVoice1 and t >= tl.voice1 then
		self.DidVoice1 = true

		self:PlayVoice(1)
		self:Announce((C.Voice.MissileBarrage[1] or {}).text or "7 THOUSAND F*CKING MISSILES", "subtitle", 2.4)
	end

	-- 2. the order
	if not self.DidVoice2 and t >= tl.voice2 then
		self.DidVoice2 = true

		self:PlayVoice(2)
		self:Announce((C.Voice.MissileBarrage[2] or {}).text or "BLOW UP THIS MAN", "alert", 2.4)
	end

	-- 3. paint it
	if not self.DidLock and t >= tl.lock then
		self.DidLock = true
		self.Phase = 1

		self:Announce("TARGET PAINTED", "subtitle", 1.3)
		EpicSWEPs.Util.PlaySound(self, C.Sounds.MissileBarrage.Siren, 0.9)

		local shake = C.Effects.Shake or {}
		EpicSWEPs.Util.ShakeAt(self:GetPos(), shake.amplitude or 16, shake.frequency or 160,
			(shake.duration or 3) + tl.spread, tl.radius * 2.4)

		self:Sync(true)
	end

	-- 4. the sky falls
	if t >= tl.impacts and t < tl.impacts + tl.spread then
		if not self.DidImpact then
			self.DidImpact = true
			self.Phase = 2

			self:Announce("INCOMING", "alert", 1.2)
			self:Sync(true)
		end

		self:LaunchMissiles()
	end

	-- 5. dust - and whatever is still queued goes out before we stop counting
	if t >= tl.impacts + tl.spread then
		self:FinishFiring()
	end

	if not self.DidEnd and t >= tl.endTime then
		self.DidEnd = true
		self.Phase = 3

		self:EndBarrage()
		return true
	end

	self:Sync()

	-- only think fast while something is actually happening
	if self.Phase == 2 then
		self:NextThink(CurTime() + 0.03)
	else
		self:NextThink(CurTime() + 0.2)
	end

	return true
end

-- fire whatever is due since the last tick, in one go
function ENT:LaunchMissiles()
	local tl = self.Timeline
	local t = CurTime() - tl.start
	local due = math.floor(self.Count * math.Clamp((t - tl.impacts) / math.max(tl.spread, 0.1), 0, 1))

	if due <= self.Sent then return end

	local first = self.Sent + 1
	local finish = math.min(due, self.Count)
	self.Sent = finish

	-- only the ones that came due since the last tick, or this is quadratic
	for i = first, finish do
		if i <= tl.damageCount and B.Damage.Enabled ~= false then
			self:DamageImpact(i)
		end
	end

	EpicSWEPs.Util.PlaySound(self, C.Sounds.MissileBarrage.Launch, 0.8, nil, math.random(90, 112))

	if finish >= self.Count then self.Phase = 3 end
end

-- Real damage, from the impacts both realms agree on.
-- Real damage, from the impacts both realms agree on (see EpicSWEPs.Util.Impacts).
function ENT:DamageImpact(i)
	local m = self.DamageImpacts and self.DamageImpacts[i]
	if not m then return 0 end

	local tl = self.Timeline
	local dmg = B.Damage
	local att = IsValid(self.EpicOwner) and self.EpicOwner or self
	local radius = dmg.Radius or 165

	if IsValid(self.EpicVictim) then
		-- the marked man gets a slightly wider target box
		if (self.EpicVictim:GetPos() - m.pos):Length2D() < tl.radius then
			radius = radius * 1.6
		end
	end

	local tr = util.TraceLine({
		start = m.pos + Vector(0, 0, 900),
		endpos = m.pos - Vector(0, 0, 900),
		mask = MASK_SHOT
	})

	local impactPos = tr.Hit and tr.HitPos or m.pos
	local hits = 0

	for _, ent in ipairs(ents.FindInSphere(impactPos, radius)) do
		if ent ~= self and ent:Health() > 0 then
			local info = DamageInfo()
			info:SetAttacker(att)
			info:SetInflictor(self)
			info:SetDamageType(dmg.DamageType or DMG_BULLET)
			info:SetDamage(dmg.PerMissile or 120)
			info:SetDamagePosition(impactPos)
			info:SetReportedPosition(impactPos)
			info:SetDamageForce(vector_up * -4000)

			ent:TakeDamageInfo(info)
			hits = hits + 1
		end
	end

	-- every so often a real explosion, so props and ragdolls go flying
	local stride = math.max(1, math.floor(#self.DamageImpacts / 24))
	if (i % stride) == 0 then
		util.BlastDamage(self, att, impactPos, radius * 1.7, 45)

		local e = EffectData()
		e:SetOrigin(impactPos)
		e:SetScale(1.7)
		e:SetMagnitude(self.Seed % 1023)	-- EffectData magnitudes are networked as 0-1023
		util.Effect("epic_explosion", e, true, true)
	end

	return hits
end

-- the last slice that did not get a tick of its own: fire it, do not lose it
function ENT:FinishFiring()
	if self.FinishFired then return end
	self.FinishFired = true

	local tl = self.Timeline

	if self.Sent < self.Count then
		local first = self.Sent + 1
		self.Sent = self.Count

		for i = first, self.Count do
			if i <= tl.damageCount and B.Damage.Enabled ~= false then
				self:DamageImpact(i)
			end
		end
	end

	self.Phase = 3
	self.Fired = self.Count
	self:Sync(true)
end

function ENT:EndBarrage()
	self:Announce("IMPACT", "alert", 1.1)

	timer.Simple(0.2, function()
		if not IsValid(self) then return end

		local e = EffectData()
		e:SetOrigin(self:GetPos())
		e:SetScale(4)
		util.Effect("epic_explosion", e, true, true)
	end)

	self:Sync(true)

	timer.Simple(C.Seq.RemoveDelay or 1.5, function()
		if IsValid(self) then self:Remove() end
	end)
end

function ENT:OnRemove()
	EpicSWEPs.Net.SendState("barrage", { e = self:EntIndex(), dead = true }, self)
end
