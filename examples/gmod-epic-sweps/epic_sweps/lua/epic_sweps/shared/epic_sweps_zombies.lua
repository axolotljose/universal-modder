--[[ Zombie spawner guts: model pool, spawn helpers and a self-managed headcount.
	NPCs and physics props are server-only, so every function here is guarded; the
	table itself exists on both realms because the entity and the renderer share it. ]]

EpicSWEPs = EpicSWEPs or {}

local C = EpicSWEPs.Config
local Z = C.ZombieSpawner

local M = {}

M.ModelPool = nil
M.Active = {}
M.ActiveCount = 0

-- If no zombie model is installed, we put a zombie brain on a citizen instead,
-- which is still funnier than 100 confused civilians standing in a ring.
local FALLBACK_MODELS = {
	"models/player/group01/male_01.mdl",
	"models/player/group01/male_03.mdl",
	"models/player/group01/male_07.mdl",
	"models/player/group01/female_02.mdl",
	"models/Freeman.mdl",
	"models/barney.mdl"
}

function M.GetModelPool()
	if M.ModelPool then return M.ModelPool end
	if not SERVER then return nil end

	local pool = {}
	local wanted = (Z.ZombieModelPool == "auto" or not Z.ZombieModelPool) and Z.Models or Z.ZombieModelPool

	if istable(wanted) then
		for _, m in ipairs(wanted) do
			if util.IsValidModel(m) then
				table.insert(pool, m)
			end
		end
	end

	if #pool == 0 then
		for _, m in ipairs(FALLBACK_MODELS) do
			if util.IsValidModel(m) then
				table.insert(pool, m)
			end
		end
	end

	if #pool == 0 then return nil end

	M.ModelPool = pool
	return pool
end

function M.CountActive()
	return M.ActiveCount
end

function M.Remember(ent)
	if not IsValid(ent) then return end

	local id = ent:EntIndex()
	if M.Active[id] then return end

	M.Active[id] = CurTime()
	M.ActiveCount = M.ActiveCount + 1
end

function M.Forget(id)
	if M.Active[id] then
		M.Active[id] = nil
		M.ActiveCount = math.max(0, M.ActiveCount - 1)
	end
end

function M.CanSpawn()
	local cap = Z.MaxZombiesAlive or 400
	return M.ActiveCount < cap
end

-- Drop the ones we spawned once they have outstayed their welcome.
function M.CullHorde(maxAge)
	if not SERVER then return end

	local now = CurTime()

	for id, born in pairs(M.Active) do
		local ent = Entity(id)

		if not IsValid(ent) then
			M.Forget(id)
		elseif maxAge and maxAge > 0 and (now - born) > maxAge then
			ent:Remove()
			M.Forget(id)
		end
	end
end

-- Snap to the floor so nobody spawns inside geometry or floats.
function M.GroundPos(origin)
	if not SERVER then return origin end

	local tr = util.TraceHull({
		start = origin + Vector(0, 0, 96),
		endpos = origin - Vector(0, 0, 640),
		mask = MASK_SHOT_HULL,
		mins = Vector(-16, -16, -16),
		maxs = Vector(16, 16, 72)
	})

	if tr.Hit then return tr.HitPos + Vector(0, 0, 2) end
	return origin
end

function M.SpawnNPC(pos, ang, model)
	if not SERVER then return nil end

	local npc = ents.Create("npc_zombie")
	if not IsValid(npc) then return nil end

	npc:SetModel(model)
	npc:SetPos(Z.FallToFloor and (pos + Vector(0, 0, 110)) or pos)
	npc:SetAngles(ang)
	npc:Spawn()

	pcall(function() npc:SetCombatRange(4096) end)

	M.Remember(npc)
	return npc
end

function M.SpawnRagdoll(pos, ang, model)
	if not SERVER then return nil end

	local rag = ents.Create("prop_ragdoll")
	if not IsValid(rag) then return nil end

	rag:SetModel(model)
	rag:SetPos(Z.FallToFloor and (pos + Vector(0, 0, 170)) or pos)
	rag:SetAngles(ang)
	rag:Spawn()

	local phys = rag:GetPhysicsObject()
	if IsValid(phys) then phys:Wake() end

	M.Remember(rag)
	return rag
end

-- The NPC barks on arrival, the prop just clatters.
function M.Vocalise(ent)
	if not SERVER or not IsValid(ent) then return end

	local snd = EpicSWEPs.Util.PickSound(C.Sounds.ZombieSpawner.Spawn)
	if not snd then return end

	ent:EmitSound(snd, SNDLVL_GTAV, math.random(92, 108), 0.75, CHAN_VOICE)
end

-- One sweep to keep the headcount honest, whoever deletes our zombies.
if SERVER then
	timer.Create("epic_sweps_zombie_cull", 5, 0, function()
		for id in pairs(M.Active) do
			if not IsValid(Entity(id)) then
				M.Forget(id)
			end
		end

		local age = Z.RemoveAfter or 0
		if age > 0 then
			M.CullHorde(age)
		end
	end)

	hook.Add("OnEntityCreated", "EpicSWEPs.Zombies", function(ent)
		if not IsValid(ent) then return end

		-- remember anything tagged by us that got created outside our own helpers
		timer.Simple(0.1, function()
			if IsValid(ent) and ent.EpicBreach then
				M.Remember(ent)
			end
		end)
	end)
end

EpicSWEPs.Zombies = M
return M
