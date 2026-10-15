--[[ 7 THOUSAND F*CKING MISSILES - you paint a target, the sky answers.

	Nothing about this weapon is subtle, including the number of missiles. The
	streaks are drawn client-side so an actual seven thousand is survivable; the
	ones that matter do real damage. ]]

local C = EpicSWEPs and EpicSWEPs.Config or {}
local B = (C.MissileBarrage or {})

SWEP.PrintName = "7000 F*cking Missiles"
SWEP.Author = "universal-modder"
SWEP.Purpose = "Seven thousand fucking missiles, then blow up this man."
SWEP.Instructions = "Primary fire: paint a target and call the barrage down on it."
SWEP.Category = "EPIC SWEPs"
SWEP.Spawnable = true
SWEP.AdminOnly = false

SWEP.Base = "weapon_base"
SWEP.ViewModel = "models/weapons/c_grenade.mdl"
SWEP.WorldModel = "models/props_junk/propane_tank001a.mdl"
SWEP.UseHands = true
SWEP.ViewModelFOV = 62

SWEP.Slot = 6
SWEP.SlotPos = 2

SWEP.HoldType = "grenade"
SWEP.DrawAmmo = true
SWEP.DrawCrosshair = true
SWEP.AutoSwitchTo = false
SWEP.AutoSwitchFrom = false

SWEP.Primary.ClipSize = 9
SWEP.Primary.DefaultClip = 9
SWEP.Primary.Automatic = false
SWEP.Primary.Ammo = "GaussEnergy"
SWEP.Secondary.ClipSize = -1
SWEP.Secondary.DefaultClip = -1
SWEP.Secondary.Automatic = false
SWEP.Secondary.Ammo = "none"

function SWEP:Initialize()
	self:SetHoldType(self.HoldType)
end

-- Anything with a body counts, including other players. Otherwise: the ground.
function SWEP:AimPos()
	local owner = self:GetOwner()
	if not IsValid(owner) then return Vector(0, 0, 0) end

	local tr = util.TraceLine({
		start = owner:GetEyePos(),
		endpos = owner:GetEyePos() + owner:GetAimVector() * 32768,
		filter = owner,
		mask = MASK_SHOT
	})

	if IsValid(tr.Entity) and (tr.Entity:IsPlayer() or tr.Entity:IsNPC()) then
		return tr.Entity:GetPos() + Vector(0, 0, 24), tr.Entity
	end

	if tr.Hit then return tr.HitPos end

	return owner:GetEyePos() + owner:GetAimVector() * 1200
end

function SWEP:Deploy()
	self:SendWeaponAnim(ACT_VM_DRAW)
	return true
end

function SWEP:PrimaryAttack()
	if not IsFirstTimePredicted() then return end

	local b = B
	self:SetNextPrimaryFire(CurTime() + 4.5)
	self:SendWeaponAnim(ACT_VM_MISSCENTER)

	if not SERVER then return end

	local owner = self:GetOwner()
	if not IsValid(owner) or not owner:IsPlayer() then return end
	if self.Cooldown then return end

	local count = EpicSWEPs.Util.CVarValue("epic_missiles_count", b.MissileCount or 7000)
	local pos, target = self:AimPos()

	local ent = EpicSWEPs.Missiles.SpawnBarrage(pos, owner, count)
	if not IsValid(ent) then
		owner:ChatPrint("[Epic SWEPs] the sky is full, no barrage entity could spawn.")
		return
	end

	if IsValid(target) then
		ent.EpicVictim = target
	end

	self:TakePrimaryAmmo(1)
	self.Cooldown = true

	timer.Simple(4.2, function()
		if IsValid(self) then self.Cooldown = nil end
	end)
end

function SWEP:SecondaryAttack()
	if not IsFirstTimePredicted() then return end
	self:SetNextSecondaryFire(CurTime() + 0.6)

	if SERVER and IsValid(self:GetOwner()) then
		self:GetOwner():ChatPrint("7000 MISSILES - primary fire paints a target. epic_missiles_count is how many fall.")
	end
end

function SWEP:Reload()
	if not IsFirstTimePredicted() then return end

	if SERVER and self:Clip1() < self.Primary.ClipSize then
		self:SetClip1(self.Primary.ClipSize)
		EpicSWEPs.Util.PlaySound(self, "weapons/grenade_pickup.wav", 0.7, SNDLVL_IDLE)
	end
end

function SWEP:TranslateActivity(act)
	return ACT_VM_MISSCENTER
end

function SWEP:DrawHUD()
	if not EpicSWEPs.CVars.epic_show_text or EpicSWEPs.CVars.epic_show_text:GetInt() == 0 then return end

	local count = EpicSWEPs.CVars.epic_missiles_count and EpicSWEPs.CVars.epic_missiles_count:GetInt() or (B.MissileCount or 7000)
	local nextIn = math.max(0, self:GetNextPrimaryFire() - CurTime())

	draw.SimpleText(util.comma(count) .. " MISSILES ON STANDBY", "Trebuchet24", ScrW() * 0.5, ScrH() - 96,
		Color(255, 170, 90, 225), TEXT_ALIGN_CENTER, TEXT_ALIGN_BOTTOM)

	if nextIn > 0.1 then
		draw.SimpleText(string.format("RE-ACQUIRING TARGET  %.1fs", nextIn), "Trebuchet18", ScrW() * 0.5, ScrH() - 74,
			Color(255, 255, 255, 160), TEXT_ALIGN_CENTER, TEXT_ALIGN_BOTTOM)
	end
end
