--[[ The renderer. All client-side visuals for both weapons live here, driven by
	the scene state the entities broadcast. Nothing in this file draws from inside
	an entity, so a hidden/off-screen barrage entity cannot ruin the show, and a
	thousand streaks cost a thousand cheap 2D sprites instead of a thousand entities. ]]

EpicSWEPs = EpicSWEPs or {}

local C = EpicSWEPs.Config
local B = C.MissileBarrage
local Z = C.ZombieSpawner
local FX = C.Effects
local U = EpicSWEPs.Util
local PT = EpicSWEPs.Particles

local Scene = {
	barrages = {},
	breaches = {},
	flash = 0,
	flashColour = Color(255, 220, 170)
}
EpicSWEPs.Scene = Scene

local TRAIL = Material("epic_sweps/missile_trail")
local FLASH = Material("epic_sweps/flash")

local function keyFor(t)
	local n = 0
	for _ in pairs(t) do n = n + 1 end
	return n
end

-- ------------------------------------------------------------ scene state --
hook.Add("epic_sweps_state", "EpicSWEPs.Render", function(id, data)
	if not istable(data) then return end

	local store = (id == "barrage") and Scene.barrages or (id == "breach") and Scene.breaches or nil
	if not store then return end

	local idx = data.e
	if not idx then return end

	if data.dead then
		store[idx] = nil
		return
	end

	local s = store[idx]
	if not s then
		s = { seenImpacts = {}, index = idx }
		store[idx] = s
	end

	s.pos = data.p or s.pos
	s.start = data.s or s.start
	s.count = data.c or s.count or (id == "barrage" and B.MissileCount or Z.ZombieCount)
	s.radius = data.r or s.radius
	s.seed = data.sd or s.seed or 0
	s.phase = data.ph or s.phase or 0
	s.spawned = data.sp or s.spawned or 0

	if s.timeline == nil or s.timelineKey ~= (s.count .. ":" .. (s.start or 0)) then
		s.timelineKey = s.count .. ":" .. (s.start or 0)

		if id == "barrage" then
			s.timeline = EpicSWEPs.Timeline.Barrage(s.pos, s.start, s.count)
			s.impacts = U.Impacts(s.pos, s.radius, s.seed, s.count, B.MaxMissilesDrawn or 2600)
			s.damageCount = U.DamageMissileCount(s.count)
		else
			s.timeline = EpicSWEPs.Timeline.Breach(s.start, s.count, Z.Waves, Z.WaveDelay)
			s.radius = s.radius or Z.HordeRadius
		end
	end
end)

local function age(s)
	return CurTime() - (s.start or CurTime())
end

-- ------------------------------------------------------------ missiles 2D ---
-- Streaks: one additive sprite per missile, drawn from the sampled impact list.
local function drawStreaks(s, t)
	local tl = s.timeline
	if not tl or not s.impacts then return end

	local vis = B.Visuals
	local rel = t - tl.impacts
	local maxDist = (FX.DrawDistance or 2600)
	maxDist = maxDist * maxDist

	local view = EyePos()
	local eye = EyeAngles()
	local drawn = 0

	surface.SetMaterial(TRAIL)

	for i = 1, #s.impacts do
		local m = s.impacts[i]
		local f = (rel - m.t * tl.spread) / m.fall

		if f >= 0 and f <= 1 then
			local head = m.pos + Vector(0, 0, vis.LaunchHeight * (1 - f))

			if (head - view):LengthSqr() < maxDist then
				local tailPos = head + Vector(0, 0, m.tail * (1 - f * 0.35))
				local a = head:ToScreen()
				local b = tailPos:ToScreen()

				if a.visible and b.visible then
					local dx, dy = b.x - a.x, b.y - a.y
					local len = math.sqrt(dx * dx + dy * dy)

					if len > 1 then
						local ang = math.deg(math.atan2(-dy, dx)) + 90
						local w = m.width * (0.35 + 0.65 * (1 - f))

						surface.SetDrawColor(255, 205, 150, 235)
						surface.DrawTexturedRectRotated(a.x + dx * 0.5, a.y + dy * 0.5, w, math.min(len, 460), ang)

						drawn = drawn + 1
					end
				end
			end
		end
	end

	s.drawn = drawn
end

-- Craters: the sampled impacts that also bite, each flashed once, plus smoke.
local function drawCraters(s, t)
	local tl = s.timeline
	local now = t - tl.impacts
	local list = s.impacts
	if not list then return end

	local want = math.Clamp(B.ExplosionCount or 44, 2, 160)
	local seen = s.seenImpacts
	local eye = EyePos()
	local sounds = {}
	local sndCount = 0

	for i = 1, math.min(want, #list) do
		local m = list[i]
		local ageT = now - m.t * tl.spread

		if ageT < 0 then break end

		if m.dmg and not seen[i] then
			seen[i] = true

			local scr = m.pos:ToScreen()
			local d = (m.pos - eye):Length()

			if scr.visible and d < 5200 then
				local size = math.Clamp(42000 / math.max(d, 60), 26, 460)

				surface.SetMaterial(FLASH)
				surface.SetDrawColor(255, 210, 150, 255)
				surface.DrawTexturedRectRotated(scr.x, scr.y, size, size, math.Rand(0, 360))

				PT.ImpactPuff(m.pos)

				if sndCount < 4 and d < 2600 then
					sndCount = sndCount + 1
					sounds[sndCount] = m.pos
				end
			end
		end
	end

	-- a few real booms per frame, close to the camera only
	for i = 1, sndCount do
		U.PlayAt(sounds[i], C.Sounds.MissileBarrage.Explosion, 1, SNDLVL_GUNFIRE)
	end
end

-- The "target painted" brackets. Pure 2D, so it reads on any map.
local function drawReticle(s, t)
	local tl = s.timeline
	local scr = s.pos:ToScreen()
	if not scr.visible then return end

	local rad = U.ScreenRadius(s.pos, s.radius or tl.radius)
	if rad < 3 then return end

	local pulse = 0.5 + 0.5 * math.sin(t * 10)
	local x0, y0 = scr.x - rad, scr.y - rad * 0.55
	local x1, y1 = scr.x + rad, scr.y + rad * 0.55
	local seg = math.Clamp(rad * 0.22, 10, 90)

	surface.SetDrawColor(255, 60 + pulse * 70, 30, 170 + pulse * 70)

	-- corners
	surface.DrawRect(math.Round(x0), math.Round(y0), math.Round(seg), 3)
	surface.DrawRect(math.Round(x0), math.Round(y0), 3, math.Round(seg))
	surface.DrawRect(math.Round(x1 - seg), math.Round(y0), math.Round(seg), 3)
	surface.DrawRect(math.Round(x1 - 3), math.Round(y0), 3, math.Round(seg))
	surface.DrawRect(math.Round(x0), math.Round(y1 - 3), math.Round(seg), 3)
	surface.DrawRect(math.Round(x0), math.Round(y1 - seg), 3, math.Round(seg))
	surface.DrawRect(math.Round(x1 - seg), math.Round(y1 - 3), math.Round(seg), 3)
	surface.DrawRect(math.Round(x1 - 3), math.Round(y1 - seg), 3, math.Round(seg))

	-- reticle bars
	surface.DrawRect(math.Round(scr.x - 2), math.Round(scr.y - rad * 0.18), 2, math.Round(rad * 0.36))
	surface.DrawRect(math.Round(scr.x - rad * 0.18), math.Round(scr.y - 1), math.Round(rad * 0.36), 2)

	-- the RPG lock-on dot, painted on the target for everyone else to see too
	if not s.markedAt or CurTime() - s.markedAt > 0.2 then
		s.markedAt = CurTime()
		PT.LockMarker(s.pos)
	end

	if FX.ShowCountdown ~= false then
		local left = math.max(0, tl.impacts - t)

		draw.SimpleText(string.format("IMPACT IN %.1f", left), "EpicSWEPs_alert_56", ScrW() * 0.5, ScrH() * 0.74,
			Color(255, 90, 55, 235), TEXT_ALIGN_CENTER, TEXT_ALIGN_CENTER)
	end
end

-- --------------------------------------------------------------- zombie 2D --
-- The breach: a green ellipse on the floor that opens and dies, plus embers.
local function drawBreachRing(s, t)
	local tl = s.timeline
	if not tl then return end

	local lifeT = 2.8
	local f = math.Clamp(t / lifeT, 0, 1)
	if f >= 1 then return end

	local alpha = 255 * (1 - f)
	local radius = 200 + t * 190
	local eye = EyePos()
	local d = (s.pos - eye):Length()
	local scr = s.pos:ToScreen()
	if not scr.visible then return end

	local flat = math.Clamp(900 / math.max(d, 80), 0.06, 1)
	local pts = {}
	local n = 28

	for i = 0, n do
		local a = math.rad((i / n) * 360 + t * 90)
		local p = s.pos + Vector(math.cos(a) * radius, math.sin(a) * radius, 6)
		local ps = p:ToScreen()

		pts[i + 1] = ps
	end

	surface.SetDrawColor(70, 255, 100, alpha)

	for i = 1, n do
		local a, b = pts[i], pts[i + 1]
		if a and b and a.visible and b.visible then
			local x = math.min(a.x, b.x)
			local y = math.min(a.y, b.y)
			surface.DrawRect(math.Round(x), math.Round(y), math.max(2, math.Round(math.abs(b.x - a.x) + 2)), 3)
			surface.DrawRect(math.Round(x), math.Round(y), 2, math.max(2, math.Round(math.abs(b.y - a.y) + 2)))
		end
	end

	surface.SetMaterial(FLASH)
	surface.SetDrawColor(60, 255, 90, alpha * 0.28)
	surface.DrawTexturedRect(math.Round(scr.x - radius * flat), math.Round(scr.y - radius * flat * 0.5),
		math.Round(radius * 2 * flat), math.Round(radius * flat))
end

-- --------------------------------------------------------------- 3D layer --
-- Beams and lights for the breach. Optional: everything here is decoration.
hook.Add("PostDrawTranslucentRenderables", "EpicSWEPs.Render", function(depth, sky)
	if sky then return end

	for _, s in pairs(Scene.breaches) do
		local t = age(s)
		if t < 2.6 and s.pos then
			local a = 200 * (1 - t / 2.6)
			local top = s.pos + Vector(0, 0, 1100 + math.sin(t * 5) * 80)

			render.SetMaterial(Material("effects/select_ring"))
			render.SuppressEngineLighting(true)
			render.DrawBeam(s.pos + Vector(0, 0, 20), top, 110 + math.sin(t * 13) * 25, 0, t * 5,
				Color(80, 255, 110, a))
			render.SuppressEngineLighting(false)

			local dl = DynamicLight(s.index + 1000)
			if dl then
				dl.pos = s.pos + Vector(0, 0, 40)
				dl.r, dl.g, dl.b = 70, 255, 100
				dl.brightness = 3.4 * (1 - t / 2.6)
				dl.Decay = 900
				dl.Size = 220
				dl.DieTime = CurTime() + 0.3
			end
		end
	end
end)

-- ------------------------------------------------------------------ audio ---
local function audioForBarrage(s, t)
	local tl = s.timeline
	if not tl then return end

	local SND = C.Sounds.MissileBarrage
	local key = "barrage_" .. s.index

	if t >= tl.lock and t < tl.impacts + 0.5 then
		U.PlayLoop(nil, key .. "_siren", SND.Siren, 0.5, 100)
	else
		U.StopLoop(key .. "_siren")
	end

	if t >= tl.impacts and t < tl.impacts + tl.spread then
		U.PlayLoop(nil, key .. "_whistle", SND.Whistle, 0.7, 100)
	else
		U.StopLoop(key .. "_whistle")
	end
end

local function cleanup(s, key)
	U.StopLoop("barrage_" .. s.index .. "_siren")
	U.StopLoop("barrage_" .. s.index .. "_whistle")
end

hook.Add("Think", "EpicSWEPs.Render", function()
	if not CLIENT then return end

	for idx, s in pairs(Scene.barrages) do
		local t = age(s)
		local tl = s.timeline

		if not tl or t > (tl.endTime + 3) then
			cleanup(s)
			Scene.barrages[idx] = nil
		else
			audioForBarrage(s, t)
		end
	end

	for idx, s in pairs(Scene.breaches) do
		local t = age(s)
		local tl = s.timeline

		if not tl or t > (tl.endTime + 3) then
			Scene.breaches[idx] = nil
		end
	end
end)

-- ------------------------------------------------------------------ paint ---
local function alivePlayer()
	local ply = LocalPlayer()
	return IsValid(ply) and ply:IsPlayer() and ply:Alive()
end

hook.Add("HUDPaint", "EpicSWEPs.Render", function()
	-- the whiteout
	if Scene.flash > 0.01 then
		local c = Scene.flashColour
		surface.SetDrawColor(c.r, c.g, c.b, math.Clamp(Scene.flash * 255, 0, 255))
		surface.DrawRect(0, 0, ScrW(), ScrH())
		Scene.flash = Scene.flash * 0.86
	else
		Scene.flash = 0
	end

	if not alivePlayer() then return end

	for _, s in pairs(Scene.barrages) do
		local tl = s.timeline
		if tl then
			local t = age(s)

			if t >= tl.lock - 0.2 and t < tl.impacts + 0.5 then
				drawReticle(s, t)
			end

			if t >= tl.impacts - 0.6 and t < tl.impacts + tl.spread + (B.Visuals.FallTime or 0.9) + 0.4 then
				drawStreaks(s, t)
			end

			if t >= tl.impacts and t < tl.impacts + tl.spread + 1.4 then
				drawCraters(s, t)
			end

			-- and the frame the sky arrives: whiteout
			if t >= tl.impacts and not s.flashed then
				s.flashed = true
				EpicSWEPs.ScreenFlash(0.8)
			end

			-- the last thing you see: the sky on fire after the storm
			if t >= tl.impacts + tl.spread and t < tl.endTime then
				local f = (t - tl.impacts - tl.spread) / math.max(tl.endTime - tl.impacts - tl.spread, 0.1)
				local scr = s.pos:ToScreen()

				if scr.visible then
					surface.SetMaterial(FLASH)
					surface.SetDrawColor(255, 130, 40, 60 * (1 - f))
					local size = 700
					surface.DrawTexturedRect(scr.x - size * 0.5, scr.y - size * 0.25, size, size * 0.5)
				end
			end
		end
	end

	for _, s in pairs(Scene.breaches) do
		local t = age(s)
		if s.pos then
			drawBreachRing(s, t)
		end
	end
end)

function EpicSWEPs.ScreenFlash(amount, colour)
	if not CLIENT then return end

	Scene.flash = math.max(Scene.flash or 0, math.Clamp(amount, 0, 1))
	Scene.flashColour = colour or Color(255, 220, 170)
end

hook.Add("LevelShutdown", "EpicSWEPs.Render", function()
	Scene.barrages = {}
	Scene.breaches = {}
	Scene.flash = 0
	U.StopAllLoops()
end)

hook.Add("LevelInit", "EpicSWEPs.Render", function()
	Scene.barrages = {}
	Scene.breaches = {}
end)

hook.Add("InitPostEntity", "EpicSWEPs.Render", function()
	PT.LoadClientEmitters()
end)

return Scene
