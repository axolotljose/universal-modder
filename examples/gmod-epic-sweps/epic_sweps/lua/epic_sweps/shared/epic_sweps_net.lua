--[[ Tiny net layer: one stream, two message kinds, no fuss.

	id 1 = announce (big text on the HUD)
	id 2 = state   (per-entity scene data, read by the renderer) ]]

EpicSWEPs = EpicSWEPs or {}

local netStr = "epic_sweps"

local M = {}
M.NetString = netStr

if SERVER then
	util.AddNetworkString(netStr)
end

-- Announce { text, style = "title"|"subtitle"|"alert", duration } on every
-- client's screen. Safe to call from either realm (the client just draws it).
function M.Announce(targets, data)
	data = data or {}

	local fx = EpicSWEPs.Config.Effects
	local payload = {
		text = data.text or "",
		style = data.style or "title",
		duration = data.duration or (fx and fx.AnnounceTime or 2.6)
	}

	if CLIENT then
		M.Receive(1, payload)
		return
	end

	net.Start(netStr)
	net.WriteUInt(0, 1)	-- kind 0 = announce
	net.WriteTable(payload)

	if targets == true then
		net.Broadcast()
	elseif istable(targets) then
		net.Send(targets)
	elseif IsValid(targets) then
		net.Send(targets)
	end
end

-- Scene state for an entity, to everyone in its PVS. data.dead = true to close it.
function M.SendState(id, data, ent)
	if CLIENT then return end

	net.Start(netStr)
	net.WriteUInt(1, 1)	-- kind 1 = scene state
	net.WriteString(id)
	net.WriteTable(data)

	if IsValid(ent) then
		net.SendPVS(ent:GetPos())
	else
		net.Broadcast()
	end
end

function M.Receive(id, data)
	if not istable(data) then return end

	if id == 1 then
		if EpicSWEPs.HUD then
			EpicSWEPs.HUD:Push(data)
		end
	else
		hook.Run("epic_sweps_state", id, data)
	end
end

if CLIENT then
	net.Receive(netStr, function(len)
		local kind = net.ReadUInt(1)

		if kind == 0 then
			M.Receive(1, net.ReadTable())
		else
			M.Receive(net.ReadString(), net.ReadTable())
		end
	end)
end

EpicSWEPs.Net = M
return M
