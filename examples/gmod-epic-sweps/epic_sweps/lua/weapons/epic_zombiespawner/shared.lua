--[[ 100 ZOMBIE SPAWNER - you throw a box, one hundred zombies come out of it,
	surrounded by an amount of visual effect that is completely out of line. ]]

local C = EpicSWEPs and EpicSWEPs.Config or {}
local Z = (C.ZombieSpawner or {})

SWEP.PrintName = "100 Zombie Spawner"
SWEP.Author = "universal-modder"
SWEP.Purpose = "Summons one hundred zombies with far too many visual effects."
SWEP.Instructions = "Primary fire: throw the box where the breach should open.\nReload: same thing, for people who like reload."
SWEP.Category = "EPIC SWEPs"
SWEP.Spawnable = true
SWEP.AdminOnly = false

SWEP.Base = "weapon_base"
SWEP.ViewModel = "models/weapons/c_stunstick.mdl"
SWEP.WorldModel = "models/props_junk/cardboard_box004a.mdl"
SWEP.UseHands = true
SWEP.ViewModelFOV = 62

SWEP.Slot = 6
SWEP.SlotPos = 1

SWEP.HoldType = "melee"
SWEP.DrawAmmo = true
SWEP.DrawCrosshair = true
SWEP.AutoSwitchTo = false
SWEP.AutoSwitchFrom = false
SWEP.BobScale = 0.4
SWEP.SwayScale = 0.4

SWEP.Primary.ClipSize = 999
SWEP.Primary.DefaultClip = 999
SWEP.Primary.Automatic = false
SWEP.Primary.Ammo = "none"
SWEP.Secondary.ClipSize = -1
SWEP.Secondary.DefaultClip = -1
SWEP.Secondary.Automatic = false
SWEP.Secondary.Ammo = "none"

function SWEP:Initialize()
	self:SetHoldType(self.HoldType)
end

-- Where the breach opens: the NPC you are looking at, or the ground behind it.
function SWEP:AimPos()
	local owner = self:GetOwner()
	if not IsValid(owner) then return Vector(0, 0, 0) end

	local tr = util.TraceLine({
		start = owner:GetEyePos(),
		endpos = owner:GetEyePos() + owner:GetAimVector() * 16384,
		filter = owner,
		mask = MASK_SHOT
	})

	if IsValid(tr.Entity) and tr.Entity:IsNPC() then
		return tr.Entity:GetPos() + Vector(0, 0, 24)
	end

	if tr.Hit then return tr.HitPos end

	return owner:GetEyePos() + owner:GetAimVector() * 900
end

function SWEP:Deploy()
	self:SendWeaponAnim(ACT_VM_DRAW)

	if SERVER and IsFirstTimePredicted() then
		EpicSWEPs.Util.PlaySound(self:GetOwner(), "weapons/grenade_draw1.wav", 0.5, SNDLVL_IDLE)
	end

	return true
end

function SWEP:PrimaryAttack()
	if not IsFirstTimePredicted() then return end
	self:SetNextPrimaryFire(CurTime() + 1.8)
	self:SendWeaponAnim(ACT_VM_MISSCENTER)

	if not SERVER then return end

	local owner = self:GetOwner()
	if not IsValid(owner) or not owner:IsPlayer() then return end
	if self.Cooldown then return end

	local count = EpicSWEPs.Util.CVarValue("epic_zombies_count", Z.ZombieCount or 100)
	local ent = EpicSWEPs.Zombies.SpawnBreach(self:AimPos(), owner, count)

	if not IsValid(ent) then
		owner:ChatPrint("[Epic SWEPs] the breach entity refused to spawn (entity limit?)")
		return
	end

	self.Cooldown = true
	timer.Simple(1.4, function()
		if IsValid(self) then self.Cooldown = nil end
	end)
end

function SWEP:SecondaryAttack()
	if not IsFirstTimePredicted() then return end
	self:SetNextSecondaryFire(CurTime() + 0.6)

	if SERVER and IsValid(self:GetOwner()) then
		self:GetOwner():ChatPrint("100 ZOMBIE SPAWNER - primary fire throws the box. epic_zombies_count sets how many come out.")
	end
end

function SWEP:Reload()
	if not IsFirstTimePredicted() then return end
	self:SendWeaponAnim(ACT_VM_DRAW)

	if SERVER then
		self:PrimaryAttack()
	end
end

-- the box buzzes in your hand before it goes off
function SWEP:GetViewModelPosition(pos, ang)
	if self.Cooldown then return pos, ang end
	return pos, ang + Angle(0, 0, math.sin(CurTime() * 12) * 3.5)
end

function SWEP:TranslateActivity(act)
	return ACT_VM_MISSCENTER
end

function SWEP:DrawHUD()
	if not EpicSWEPs.HUD then return end

	draw.SimpleText("ZOMBIES ARMED: " .. (EpicSWEPs.CVars.epic_zombies_count and EpicSWEPs.CVars.epic_zombies_count:GetInt() or "?"),
		"Trebuchet24", ScrW() * 0.5, ScrH() - 96, Color(150, 255, 130, 225), TEXT_ALIGN_CENTER, TEXT_ALIGN_BOTTOM)
end
