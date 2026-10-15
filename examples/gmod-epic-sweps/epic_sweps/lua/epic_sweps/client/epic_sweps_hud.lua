--[[ The big glowing text. Fed by EpicSWEPs.Net.Announce from either realm. ]]

EpicSWEPs = EpicSWEPs or {}

local C = EpicSWEPs.Config
local E = C.Effects

local HUD = {}
HUD.queue = {}

local POP = 0.18
local FADE = 0.45

local function fontFor(style, size)
	return "EpicSWEPs_" .. style .. "_" .. size
end

function HUD:InitFonts()
	if not EpicSWEPs.FontsReady then
		surface.CreateFont("EpicSWEPs_title_64", {
			font = "Impact", size = 64, weight = 900, extended = true,
			antialias = true
		})
		surface.CreateFont("EpicSWEPs_title_128", {
			font = "Impact", size = 128, weight = 900, extended = true, antialias = true
		})
		surface.CreateFont("EpicSWEPs_subtitle_32", {
			font = "Trebuchet MS", size = 32, weight = 800, extended = true, antialias = true
		})
		surface.CreateFont("EpicSWEPs_alert_56", {
			font = "Impact", size = 56, weight = 900, extended = true, antialias = true
		})
		EpicSWEPs.FontsReady = true
	end
end

function HUD:Push(data)
	if EpicSWEPs.CVars and EpicSWEPs.CVars.epic_show_text and EpicSWEPs.CVars.epic_show_text:GetInt() == 0 then return end

	table.insert(self.queue, {
		text = data.text or "",
		style = data.style or "title",
		duration = data.duration or E.AnnounceTime or 2.6,
		start = CurTime(),
		color = data.color
	})

	while #self.queue > (E.MaxAnnouncements or 4) do
		table.remove(self.queue, 1)
	end
end

local function colorFor(style, custom)
	if custom then return custom end

	if style == "subtitle" then
		return Color(255, 255, 255, 255)
	elseif style == "alert" then
		return Color(255, 70, 40, 255)
	end

	return Color(255, 235, 180, 255)
end

function HUD:DrawEntry(entry, t, index)
	local dur = math.max(entry.duration, 0.6)
	local life = t / dur

	-- pop in, sit, fade out
	local alpha = 255
	if t < POP then
		alpha = 255 * (t / POP)
	elseif life > (1 - FADE / dur) then
		alpha = 255 * math.Clamp((dur - t) / FADE, 0, 1)
	end

	local scale = 1
	if t < POP then
		scale = Lerp(t / POP, 1.6, 1)
	elseif life > 0.9 then
		scale = Lerp((life - 0.9) / 0.1, 1, 1.06)
	end

	local jitter = 0
	if t < POP * 1.6 then
		jitter = (1 - t / (POP * 1.6)) * 6
	end

	local style = entry.style
	local size = (style == "title" and 64 or (style == "alert" and 56 or 32))
	local font = fontFor(style, size)

	surface.SetFont(font)
	local w, h = surface.GetTextSize(entry.text)
	w, h = w * scale, h * scale

	local cx = ScrW() * 0.5 + math.Rand(-1, 1) * jitter
	local cy = ScrH() * (style == "subtitle" and 0.60 or 0.38) + math.Rand(-1, 1) * jitter + (index - 1) * 46

	local col = colorFor(style, entry.color)
	col.a = math.Clamp(alpha, 0, 255)

	-- cheap glow: hard offsets instead of a real blur
	surface.SetTextColor(0, 0, 0, col.a * 0.85)
	for _, off in ipairs({ { 4, 4 }, { -3, 3 } }) do
		surface.SetTextPos(math.Round(cx - w * 0.5 + off[1]), math.Round(cy - h * 0.5 + off[2]))
		surface.SetText(font, entry.text)
	end

	-- additive bloom-ish pass
	if style ~= "subtitle" then
		local addA = col.a * 0.45

		draw.SimpleText(entry.text, font, cx + 1, cy - 1, Color(col.r, col.g, col.b, addA), TEXT_ALIGN_CENTER, TEXT_ALIGN_CENTER, 1)
		draw.SimpleText(entry.text, font, cx - 1, cy + 1, Color(255, 120, 40, addA * 0.6), TEXT_ALIGN_CENTER, TEXT_ALIGN_CENTER, 1)
	end

	draw.SimpleText(entry.text, font, cx, cy, col, TEXT_ALIGN_CENTER, TEXT_ALIGN_CENTER, 1)

	-- underline flare for titles
	if style == "title" or style == "alert" then
		local barW = w * 0.9 * math.Clamp(t / 0.3, 0, 1)
		surface.SetDrawColor(col.r, col.g, col.b, col.a * 0.7)
		surface.DrawRect(cx - barW * 0.5, cy + h * 0.62, barW, 3)
	end
end

function HUD:Paint()
	if #self.queue == 0 then return end

	self:InitFonts()

	local now = CurTime()

	-- cinematic bars, while anything is on screen
	if E.ShowCinematicBars then
		local barH = math.Round(ScrH() * 0.055)
		surface.SetDrawColor(0, 0, 0, 200)
		surface.DrawRect(0, 0, ScrW(), barH)
		surface.DrawRect(0, ScrH() - barH, ScrW(), barH)
	end

	for i = #self.queue, 1, -1 do
		local entry = self.queue[i]
		local t = now - entry.start

		if t > entry.duration then
			table.remove(self.queue, i)
		else
			self:DrawEntry(entry, t, #self.queue - i + 1)
		end
	end
end

hook.Add("HUDPaint", "EpicSWEPs.Announce", function()
	HUD:Paint()
end)

hook.Add("OnScreenSizeChanged", "EpicSWEPs.Announce", function()
	HUD:InitFonts()
end)

EpicSWEPs.HUD = HUD
return HUD
