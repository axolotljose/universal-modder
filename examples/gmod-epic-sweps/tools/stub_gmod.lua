--[[ Stub GMod environment for offline testing (NOT part of the addon).

	Everything here is a fake: enough of the Source/GMod Lua API to load the addon's
	modules, entities, effects and weapons in plain Lua 5.x, step a fake clock, and
	see whether anything actually errors. Run it with tools/run_smoke_test.js (fengari).

	This is a dev tool. GMod never sees this file. ]]

local api = {}

local V, A

local function clamp(v, lo, hi) return v < lo and lo or (v > hi and hi or v) end

V = {}
V.__index = V
function api.Vector(x, y, z)
	return setmetatable({ x = x or 0, y = y or 0, z = z or 0 }, V)
end

local function isnum(v) return type(v) == "number" end

V.__add = function(a, b) return api.Vector(a.x + b.x, a.y + b.y, a.z + b.z) end
V.__sub = function(a, b) return api.Vector(a.x - b.x, a.y - b.y, a.z - b.z) end
V.__mul = function(a, b)
	if isnum(b) then return api.Vector(a.x * b, a.y * b, a.z * b) end
	return api.Vector(a.x * b.x, a.y * b.y, a.z * b.z)
end
V.__div = function(a, b)
	if isnum(b) then return api.Vector(a.x / b, a.y / b, a.z / b) end
	return api.Vector(a.x / b.x, a.y / b.y, a.z / b.z)
end
V.__unm = function(a) return api.Vector(-a.x, -a.y, -a.z) end
V.__eq = function(a, b) return a.x == b.x and a.y == b.y and a.z == b.z end
V.__tostring = function(a) return string.format("( %.1f, %.1f, %.1f )", a.x, a.y, a.z) end

function V:Length() return math.sqrt(self.x ^ 2 + self.y ^ 2 + self.z ^ 2) end
function V:LengthSqr() return self.x ^ 2 + self.y ^ 2 + self.z ^ 2 end
function V:Length2D() return math.sqrt(self.x ^ 2 + self.y ^ 2) end
function V:Length2DSqr() return self.x ^ 2 + self.y ^ 2 end
function V:Distance(o) return (self - o):Length() end
function V:Normalize()
	local l = self:Length()
	if l > 0 then self.x, self.y, self.z = self.x / l, self.y / l, self.z / l end
	return self
end
function V:GetNormal() local c = api.Vector(self.x, self.y, self.z) return c:Normalize() end
function V:Dot(o) return self.x * o.x + self.y * o.y + self.z * o.z end
function V:Cross(o)
	return api.Vector(self.y * o.z - self.z * o.y, self.z * o.x - self.x * o.z, self.x * o.y - self.y * o.x)
end
function V:ToScreen()
	-- pretend the camera looks at the origin from 1200 units out
	local d = (self - api.state.eyePos):Length()
	return { x = 960 + self.x * 0.4, y = 540 - (self.z - 100) * 0.8, visible = d < 20000 }
end
function V:Angle()
	local atan2 = math.atan2 or math.atan
	return api.Angle(0, math.deg(atan2(self.y, self.x)), 0)
end
function V:GetLeft() return api.Vector(-self.y, self.x, 0) end
function V:AlmostEqual() return true end

A = {}
A.__index = A
function api.Angle(p, y, r) return setmetatable({ p = p or 0, y = y or 0, r = r or 0 }, A) end
A.__add = function(a, b) return api.Angle(a.p + b.p, a.y + b.y, a.r + b.r) end
A.__sub = function(a, b) return api.Angle(a.p - b.p, a.y - b.y, a.r - b.r) end
A.__mul = function(a, b)
	if isnum(b) then return api.Angle(a.p * b, a.y * b, a.r * b) end
	return api.Angle(a.p * b.p, a.y * b.y, a.r * b.r)
end
A.__unm = function(a) return api.Angle(-a.p, -a.y, -a.r) end
function A:Forward() return api.Vector(math.cos(math.rad(self.y)) * math.cos(math.rad(self.p)), math.sin(math.rad(self.y)) * math.cos(math.rad(self.p)), -math.sin(math.rad(self.p))) end
function A:Up() return api.Vector(0, 0, 1) end
function A:Right() return api.Vector(1, 0, 0) end
function A:AngleNormal() return self end

local COLOR = {}
COLOR.__index = COLOR
function api.Color(r, g, b, a)
	return setmetatable({ r = r or 255, g = g or 255, b = b or 255, a = a or 255 }, COLOR)
end

-- ------------------------------------------------------------ runtime state
api.state = {
	time = 0,
	eyePos = api.Vector(0, -1200, 600),
	width = 1920,
	height = 1080,
	realm = "SERVER",
	ents = {},
	nextID = 1,
	metatables = {},
	hooks = {},
	timers = {},
	timerSeq = 0,
	netlog = {},
	netRecv = {},
	netIn = nil,
	netPtr = 1,
	convars = {},
	effects = {},
	materials = {},
	drawCalls = {},
	soundCalls = {},
	log = {},
	files = {},
	entitiesByClass = {},
	createdNPCs = 0,
	damageEvents = 0,
	particleCalls = 0,
	currentEffect = nil
}

local S = api.state

local function record(kind, n)
	S.drawCalls[kind] = (S.drawCalls[kind] or 0) + (n or 1)
end

-- ------------------------------------------------------------------- types

local entityBase = {}
entityBase.__index = function(t, k)
	local known = rawget(entityBase, k)
	if known ~= nil then return known end

	-- record it and hand back a no-op; the smoke test prints anything unexpected here
	S.unknown = S.unknown or {}
	S.unknown[tostring(k)] = (S.unknown[tostring(k)] or 0) + 1

	local stub = function() return nil end
	rawset(entityBase, k, stub)
	return stub
end

-- minimal weapon_base: the bits our SWEPs touch
SWEP_BASE = {
	SetHoldType = function(self, h) self.holdtype = h end,
	SetNextPrimaryFire = function(self, t) self.nextPrimary = t end,
	GetNextPrimaryFire = function(self) return self.nextPrimary or 0 end,
	SetNextSecondaryFire = function(self, t) self.nextSecondary = t end,
	SendWeaponAnim = function(self, a) self.lastAnim = a end,
	GetOwner = function(self) return self.owner end,
	TakePrimaryAmmo = function(self, n) self.clip = math.max(0, (self.clip or self.Primary.ClipSize) - n) end,
	Clip1 = function(self) return self.clip or self.Primary.ClipSize end,
	SetClip1 = function(self, v) self.clip = v end,
	EmitSound = function(self, name, ch, pitch, vol) table.insert(api.state.soundCalls, { ent = "SWEP", sound = name }) end,
	GetViewModelPosition = function(self, p, a) return p, a end,
	Primary = { ClipSize = -1, DefaultClip = -1, Automatic = false, Ammo = "none" },
	Secondary = { ClipSize = -1, DefaultClip = -1, Automatic = false, Ammo = "none" }
}
api.SWEP_BASE = SWEP_BASE

function entityBase:SetModel(m) self.model = m end
function entityBase:GetModel() return self.model or "models/error.mdl" end
function entityBase:SetPos(p) self.pos = api.Vector(p.x, p.y, p.z) end
function entityBase:GetPos() return self.pos or api.Vector(0, 0, 0) end
function entityBase:SetAngles(a) self.ang = a end
function entityBase:GetAngles() return self.ang or api.Angle(0, 0, 0) end
function entityBase:SetNoDraw(v) self.nodraw = v end
function entityBase:DrawShadow(v) self.shadow = v end
function entityBase:SetColor(c) self.colour = c end
function entityBase:SetMaterial(m) self.material = m end
function entityBase:SetTrigger(v) self.trigger = v end
function entityBase:SetCollisionGroup(v) self.collgroup = v end
function entityBase:SetSolid(v) self.solid = v end
function entityBase:SetMoveType(v) self.movetype = v end
function entityBase:PhysicsInit(v) self.physics = v end
function entityBase:Use(v) self.use = v end
function entityBase:SetVar(k, v) self.vars = self.vars or {}; self.vars[k] = v end
function entityBase:GetVar(k, d)
	self.vars = self.vars or {}
	if self.vars[k] == nil then return d end
	return self.vars[k]
end
function entityBase:EntIndex() return self.index end
function entityBase:Spawn() self.spawned = true end
function entityBase:Initialize() end
function entityBase:GetClass() return self.classname end
function entityBase:SetNotSolid(v) self.notsolid = v end
function entityBase:SetRenderMode(v) self.rendermode = v end
function entityBase:SetRenderFX(v) self.renderfx = v end
function entityBase:SetSkin(v) self.skin = v end
function entityBase:SetBodygroup(a, b) self.bodygroup = b end
function entityBase:SetSequence(v) self.sequence = v end
function entityBase:SetCycle(v) self.cycle = v end
function entityBase:SetPlaybackRate(v) self.rate = v end
function entityBase:GetVelocity() return api.Vector(0, 0, 0) end
function entityBase:SetVelocity(v) self.vel = v end
function entityBase:SetAbsVelocity(v) self.vel = v end
function entityBase:SetOwner(v) self.ownerEnt = v end
function entityBase:GetOwner() return self.ownerEnt end
function entityBase:SetParent(v) self.parent = v end
function entityBase:SetMoveParent(v) self.moveparent = v end
function entityBase:EnableMotion(v) self.motion = v end
function entityBase:Wake() self.awake = true end
function entityBase:IsOnFire() return false end
function entityBase:IsOnGround() return true end
function entityBase:EyeAngles() return api.Angle(0, 0, 0) end
function entityBase:GetAimEntity() return nil end
function entityBase:GetForward() return api.Vector(0, 1, 0) end
function entityBase:NetworkSetTable(k, v) self.nettable = v end
function entityBase:GetNetworkedTable(k) return self.nettable end
function entityBase:SetNetworkedInt(k, v) self[k] = v end
function entityBase:GetNetworkedInt(k) return self[k] end
function entityBase:SetNetworkedAngle(k, v) self[k] = v end
function entityBase:SetNetworkedVector(k, v) self[k] = v end
function entityBase:Kill() self:Remove() end
function entityBase:SetUseType(v) self.usetype = v end
function entityBase:Health() return self.health or 100 end
function entityBase:SetHealth(h) self.health = h end
function entityBase:MaxHealth() return self.maxhealth or 100 end
function entityBase:IsPlayer() return self.classname == "player" end
function entityBase:IsNPC() return self.isNPC or false end
function entityBase:IsValid() return true end
function entityBase:Alive() return self.alive ~= false end
function entityBase:Remove()
	self.dead = true
	S.ents[self.index] = nil
end
-- Entity:EmitSound( name, soundLevel = 75, pitchPercent = 100, volume = 1, channel = CHAN_AUTO, ... )
function entityBase:EmitSound(name, level, pitch, volume, channel)
	if type(name) ~= "string" then error("EmitSound: sound name must be a string") end
	if string.match(name, "^%s") or string.match(name, "%s$") then error("EmitSound: no whitespace allowed in the sound name") end
	if level ~= nil and (type(level) ~= "number" or level < 0 or level > 511) then
		error("EmitSound: arg 2 is soundLevel (0-511), got " .. tostring(level) .. " - wrong argument order?")
	end
	if pitch ~= nil and (type(pitch) ~= "number" or pitch < 0 or pitch > 255) then
		error("EmitSound: arg 3 is pitchPercent (0-255), got " .. tostring(pitch))
	end
	if volume ~= nil and (type(volume) ~= "number" or volume < 0 or volume > 1) then
		error("EmitSound: arg 4 is volume (0-1), got " .. tostring(volume) .. " - wrong argument order?")
	end
	table.insert(S.soundCalls, { ent = self.index, sound = name, vol = volume, level = level })
end
function entityBase:NextThink(t) self.nextThink = t or S.time end
function entityBase:GetPhysicsObject() return self end
function entityBase:EnableMotion(v) self.motion = v end
function entityBase:Wake() self.awake = true end
function entityBase:SetCombatRange(v) self.combatRange = v end
function entityBase:SetSchedule(v) self.schedule = v end
function entityBase:TakeDamageInfo(info)
	S.damageEvents = S.damageEvents + 1
	return true
end
function entityBase:GetEyePos() return api.Vector(0, 0, 64) end
function entityBase:GetAimVector() return api.Vector(0, 1, 0) end
function entityBase:ChatPrint(t) table.insert(S.log, "CHAT: " .. tostring(t)) end
function entityBase:GetNetworkedVector() return self:GetPos() end
function entityBase:OBBMins() return api.Vector(-16, -16, -16) end
function entityBase:OBBMaxs() return api.Vector(16, 16, 16) end

local function classFor(class)
	local ENT = S.entitiesByClass[class]
	if not ENT then return entityBase end
	if not S.metatables[class] then
		S.metatables[class] = { __index = setmetatable(ENT, { __index = entityBase }) }
	end
	return S.metatables[class].__index
end

function api.newEntity(class)
	local e = setmetatable({ classname = class, index = S.nextID, pos = api.Vector(0, 0, 0) }, { __index = classFor(class) })
	S.nextID = S.nextID + 1
	return e
end

-- ------------------------------------------------------------------ globals
local g = api.globals

local function setup()
	g = {}
	api.globals = g
	S.time = 0
	S.ents = {}
	S.hooks = {}
	S.timers = {}
	S.netlog = {}
	S.netIn = nil
	S.soundCalls = {}
	S.drawCalls = {}
	S.log = {}
	S.damageEvents = 0
	S.createdNPCs = 0
	S.particleCalls = 0

	g.SERVER = (S.realm == "SERVER")
	g.CLIENT = (S.realm == "CLIENT")

	g.Vector = api.Vector
	g.Angle = api.Angle
	g.Color = api.Color
	g.VectorOrigin = api.Vector(0, 0, 0)
	g.vector_origin = api.Vector(0, 0, 0)
	g.vector_up = api.Vector(0, 0, 1)
	g.angle_zero = api.Angle(0, 0, 0)
	g.ANGLE_ZERO = api.Angle(0, 0, 0)
	g.null = 0
	g.NULL = setmetatable({ IsNull = function() return true end }, { __tostring = function() return "Entity [NULL]" end })

	g.IsValid = function(x)
		if x == nil then return false end
		if type(x) == "table" then
			if x.classname == "worldspawn" then return true end
			if x.index then return not x.dead and S.ents[x.index] ~= nil end
			return true
		end
		return false
	end
	g.isvector = function(v) return type(v) == "table" and v.x ~= nil end
	g.isangle = function(v) return type(v) == "table" and v.p ~= nil and v.y ~= nil end
	g.isnumber = function(v) return type(v) == "number" end
	g.isstring = function(v) return type(v) == "string" end
	g.istable = function(v) return type(v) == "table" end
	g.isbool = function(v) return type(v) == "boolean" end
	g.isfunction = function(v) return type(v) == "function" end
	g.isentity = function(v) return type(v) == "table" and v.index ~= nil end
	g.IsFirstTimePredicted = function() return true end
	g.ScrW = function() return S.width end
	g.ScrH = function() return S.height end
	g.VisionFilteringTable = nil
	g.CurTime = function() return S.time end
	g.RealTime = function() return S.time end
	g.FrameTime = function() return 0.015 end
	g.TickInterval = function() return 0.015 end
	g.Lerp = function(f, a, b) return a + (b - a) * f end
	g.Clamp = function(v, a, b) return clamp(v, a, b) end
	g.Msg = function(...)
		local t = {}
		for i = 1, select("#", ...) do t[i] = tostring((select(i, ...))) end
		table.insert(S.log, "MSG: " .. table.concat(t))
		return #t
	end
	g.MsgN = function(...) g.Msg(...) end
	g.ErrorNoHalt = function(...) table.insert(S.log, "ERR: " .. tostring((select(1, ...)))) end
	g.ErrorNoHaltWithStack = g.ErrorNoHalt
	g.pcall = pcall
	g.type = type
	g.tostring = tostring
	g.tonumber = tonumber
	g.tostring = tostring

	-- math helpers GMod adds
	g.math = setmetatable({
		Clamp = function(v, a, b) return clamp(v, a, b) end,
		Round = function(v, dp) dp = dp or 0 local m = 10 ^ dp return math.floor(v * m + 0.5) / m end,
		Rand = function(a, b) if a == nil then return math.random() end return math.random() * (b - a) + a end,
		Approach = function(a, t, s) if a > t then return math.max(a - s, t) end return math.min(a + s, t) end,
		LinearRate = function(x) x = clamp(x, 0, 1) return x * x * (3 - 2 * x) end,
		atan2 = math.atan2 or function(y, x) return math.atan(y, x) end,	-- GMod is Lua 5.1
		frametime = function() return 0.015 end,
		ApproxEqual = function(a, b) return math.abs(a - b) < 0.0001 end
	}, { __index = math })

	g.table = setmetatable({
		Insert = table.insert,
		Remove = table.remove,
		Random = function(t) if #t == 0 then return nil end return t[math.random(#t)] end,
		GetFirstKey = function(t) return next(t) end,
		Count = function(t) local n = 0 for _ in pairs(t) do n = n + 1 end return n end,
		Empty = function(t) return next(t) == nil end,
		Concat = table.concat
	}, { __index = table })

	g.string = setmetatable({
		lower = string.lower,
		find = string.find,
		formatted = string.format,
		GetExtensionFromFilename = function(f) return string.match(f, "%.(%w+)$") end,
		StripExtension = function(f) return (string.gsub(f, "%.%w+$", "")) end
	}, { __index = string })

	-- ------------------------------------------------------------- timers
	g.timer = {
		Create = function(name, period, reps, fn)
			S.timers[name] = { period = period, reps = reps, fn = fn, next = S.time + period }
		end,
		Simple = function(delay, fn)
			S.timerSeq = S.timerSeq + 1
			S.timers["tmp" .. S.timerSeq] = { once = true, next = S.time + (delay or 0), fn = fn }
		end,
		Exists = function(name) if S.timers[name] then return true end return false end,
		Stop = function(name) S.timers[name] = nil end,
		Remove = function(name) S.timers[name] = nil end,
		Start = function(name) if S.timers[name] then S.timers[name].next = S.time end end
	}

	g.hook = {
		Add = function(name, ident, fn)
			S.hooks[name] = S.hooks[name] or {}
			S.hooks[name][ident] = fn
		end,
		Remove = function(name, ident)
			if S.hooks[name] then S.hooks[name][ident] = nil end
		end,
		Run = function(name, a, b, c, d)
			local t = S.hooks[name]
			if not t then return end
			for _, fn in pairs(t) do fn(a, b, c, d) end
		end
	}
	g.hook.Call = g.hook.Run

	-- ------------------------------------------------------------- convars
	local function convar(name, def, flags, help)
		local c = {}
		c.name = name
		c.value = tostring(def)
		c.GetInt = function() return tonumber(c.value) or 0 end
		c.GetFloat = function() return tonumber(c.value) or 0 end
		c.GetString = function() return c.value end
		c.GetBool = function() return (tonumber(c.value) or 0) ~= 0 end
		c.SetInt = function(v) c.value = tostring(v) end
		c.SetFloat = function(v) c.value = tostring(v) end
		c.SetString = function(v) c.value = tostring(v) end

		S.convars[name] = c
		return c
	end

	g.CreateConVar = function(name, def, flags, help) return convar(name, def, flags, help) end
	g.GetConVar = function(name)
		local c = S.convars[name]
		return c and c:GetString() or nil
	end
	g.RunConsoleCommand = function() end

	g.material = { GetMaterial = function(n) return { name = n } end, Set = function() end }
	g.Material = function(name, fallback)
		S.materials[name] = (S.materials[name] or 0) + 1
		return { name = name, __tostring = function() return name end }
	end

	g.file = {
		Exists = function(p, root)
			if S.files[p] ~= nil then return S.files[p] end
			return true	-- pretend everything shipped is there
		end,
		Find = function() return {}, {} end,
		Read = function() return nil end
	}

	g.render = {
		SetMaterial = function(m) record("render.SetMaterial") end,
		SuppressEngineLighting = function(v) record("render.SuppressEngineLighting") end,
		DrawBeam = function(a, b, w, s, e, c) record("render.DrawBeam"); if type(a) ~= "table" then error("DrawBeam needs a vector") end end,
		DrawQuadEasy = function(p, n, w, h, c, a) record("render.DrawQuadEasy"); if type(p) ~= "table" then error("DrawQuadEasy needs a vector") end end,
		DrawSphere = function() record("render.DrawSphere") end,
		SetBlend = function() end,
		SetColorModulation = function(r, gg, b)
			if r == nil then error("SetColorModulation needs numbers") end
		end,
		SetRenderTarget = function() end
	}

	g.surface = {
		CreateFont = function(name, t) S.fonts = S.fonts or {}; S.fonts[name] = t end,
		SetFont = function(f) S.font = f end,
		GetTextSize = function(t) return #tostring(t) * 22, 24 end,
		SetTextPos = function(x, y)
			if type(x) ~= "number" or type(y) ~= "number" then error("SetTextPos needs numbers") end
			if x ~= x or y ~= y then error("SetTextPos got NaN") end
		end,
		SetText = function(f, t) record("surface.SetText") end,
		SetTextColor = function() end,
		SetDrawColor = function(r, gg, b, a)
			if r == nil then error("SetDrawColor needs a colour") end
		end,
		DrawRect = function(x, y, w, h)
			if w ~= w or h ~= h or x ~= x or y ~= y then error("DrawRect got NaN: " .. tostring(x) .. "," .. tostring(y) .. "," .. tostring(w) .. "," .. tostring(h)) end
			if w < 0 or h < 0 then error("DrawRect with negative size: " .. tostring(w) .. "x" .. tostring(h)) end
			record("surface.DrawRect")
		end,
		SetMaterial = function(m) record("surface.SetMaterial") end,
		DrawTexturedRect = function(x, y, w, h)
			if w ~= w or h ~= h then error("DrawTexturedRect got NaN") end
			if w < 0 or h < 0 then error("DrawTexturedRect negative size: " .. tostring(w) .. "x" .. tostring(h)) end
			record("surface.DrawTexturedRect")
		end,
		DrawTexturedRectRotated = function(x, y, w, h, ang)
			if w ~= w or h ~= h or ang ~= ang then error("DrawTexturedRectRotated got NaN") end
			if w < 0 or h < 0 then error("DrawTexturedRectRotated negative size: " .. tostring(w) .. "x" .. tostring(h)) end
			record("surface.streak")
		end
	}

	g.draw = {
		NoTexture = function() record("draw.NoTexture") end,
		DrawPoly = function(t) record("draw.DrawPoly") end,
		SimpleText = function(text, font, x, y, col, ax, ay, shadow)
			if type(x) ~= "number" or type(y) ~= "number" then error("SimpleText needs numeric position, got " .. type(x) .. "," .. type(y)) end
			if x ~= x or y ~= y then error("SimpleText got NaN") end
			if type(text) ~= "string" then error("SimpleText needs a string, got " .. type(text)) end
			record("draw.SimpleText")
		end
	}

	g.cam = {
		Start2D = function() end,
		End2D = function() end,
		Start3D = function() end,
		End3D = function() end,
		Draw2D = function() end
	}

	g.DynamicLight = function(id)
		local dl = setmetatable({}, { __newindex = function(t, k, v)
			if v == nil then error("DynamicLight field " .. k .. " is nil") end
			if type(v) == "number" and v ~= v then error("DynamicLight field " .. k .. " is NaN") end
			rawset(t, k, v)
		end })
		record("DynamicLight")
		return dl
	end

	g.net = {
		AddNetworkString = function(name) S.netStrings = S.netStrings or {}; S.netStrings[name] = true end,
		Start = function(name) S.netOut = { str = name, vals = {} } end,
		WriteUInt = function(v, bits)
			if type(v) ~= "number" then error("net.WriteUInt wants a number, got " .. type(v)) end
			if bits and bits < 32 and v >= 2 ^ bits then
				error(string.format("net.WriteUInt: %d does not fit in %d bits", v, bits))
			end
			table.insert(S.netOut.vals, { "uint", v })
		end,
		WriteString = function(v)
			if type(v) ~= "string" then error("net.WriteString wants a string, got " .. type(v)) end
			table.insert(S.netOut.vals, { "string", v })
		end,
		WriteTable = function(v)
			if type(v) ~= "table" then error("net.WriteTable wants a table, got " .. type(v)) end
			table.insert(S.netOut.vals, { "table", v })
		end,
		WriteVector = function(v) table.insert(S.netOut.vals, { "vector", v }) end,
		WriteFloat = function(v) table.insert(S.netOut.vals, { "float", v }) end,
		WriteBool = function(v) table.insert(S.netOut.vals, { "bool", v }) end,
		Broadcast = function()
			if not S.netOut then error("net.Broadcast without net.Start") end
			table.insert(S.netlog, { str = S.netOut.str, vals = S.netOut.vals, to = "broadcast" })
			S.netOut = nil
		end,
		SendPVS = function(p)
			table.insert(S.netlog, { str = S.netOut.str, vals = S.netOut.vals, to = "pvs" })
			S.netOut = nil
		end,
		Send = function(t)
			table.insert(S.netlog, { str = S.netOut.str, vals = S.netOut.vals, to = "send" })
			S.netOut = nil
		end,
		Receive = function(name, fn)
			S.netRecv[name] = fn
		end,
		__pop = function(want)
			local v = S.netIn[1]
			table.remove(S.netIn, 1)
			if not v then error("net read past end of message (wanted " .. want .. ")") end
			if v[1] ~= want then
				error("net type mismatch: wrote " .. v[1] .. ", reader wants " .. want)
			end
			return v[2]
		end
	}
	g.net.ReadUInt = function(bits) return g.net.__pop("uint") end
	g.net.ReadString = function() return g.net.__pop("string") end
	g.net.ReadTable = function() return g.net.__pop("table") end
	g.net.ReadVector = function() return g.net.__pop("vector") end
	g.net.ReadFloat = function() return g.net.__pop("float") end
	g.net.ReadBool = function() return g.net.__pop("bool") end

	g.Effects = {
		RegisterEffect = function(name, t) S.effects[name] = t end
	}

	g.EffectData = function()
		local d = { origin = api.Vector(0, 0, 0), scale = 1, mag = 0, ent = nil }
		return {
			SetOrigin = function(_, v) d.origin = v end,
			GetOrigin = function() return d.origin end,
			SetScale = function(_, v) d.scale = v end,
			GetScale = function() return d.scale end,
			SetMagnitude = function(_, v) d.mag = v end,
			GetMagnitude = function() return d.mag end,
			SetEntity = function(_, v) d.ent = v end,
			GetEntity = function() return d.ent end
		}
	end

	g.util = {
		AddNetworkString = g.net.AddNetworkString,
		PrecacheParticle = function() end,
		TraceLine = function(t)
			return { Hit = true, HitPos = api.Vector(t.start and t.start.x or 0, t.start and t.start.y or 0, 0),
				Fraction = 0.6, Entity = nil, Normal = api.Vector(0, 0, 1) }
		end,
		TraceHull = function(t)
			return { Hit = true, HitPos = api.Vector(t.start.x, t.start.y, 0), Fraction = 0.5, Entity = nil }
		end,
		BlastDamage = function(inf, att, pos, radius, dmg)
			if type(pos) ~= "table" then error("util.BlastDamage needs a position") end
			S.blasts = (S.blasts or 0) + 1
		end,
		Effect = function(name, data, a, b)
			if type(name) ~= "string" then error("util.Effect wants a name, got " .. type(name)) end
			if not S.effects[name] and not ENGINE_EFFECTS[name] then
				error("util.Effect: unknown effect '" .. name .. "' (not a Lua effect and not a GMod default effect)")
			end
			if not S.effects[name] then
				S.effectFired = S.effectFired or {}
				S.effectFired[name] = (S.effectFired[name] or 0) + 1
				return
			end
			S.effectFired = S.effectFired or {}
			S.effectFired[name] = (S.effectFired[name] or 0) + 1
			-- run the client-side effect right away, it is a good proxy for errors
			local E = S.effects[name]
			local inst = setmetatable({}, { __index = E })
			inst.Init = E.Init
			inst.Think = E.Think
			inst.Render = E.Render
			inst.Origin = data:GetOrigin()
			-- step the effect on its own clock: it must not steal frames from the world
			local realTime = S.time
			local ok, err = pcall(function()
				inst:Init(data)
				for i = 1, 4 do
					S.time = realTime + i * 0.1
					inst:Think()
					inst:Render()
				end
			end)
			S.time = realTime
			if not ok then error("effect " .. name .. " failed: " .. tostring(err)) end
		end,
		IsValidModel = function(m)
			S.modelsChecked = (S.modelsChecked or 0) + 1
			-- pretend the HL2 zombie + citizen models are installed
			local known = {
				["models/zombie_male.mdl"] = true,
				["models/zombie_player0.mdl"] = true,
				["models/barney.mdl"] = true,
				["models/player/group01/male_01.mdl"] = true
			}
			return known[m] == true
		end,
		ScreenShake = function(pos, amp, freq, dur, radius)
			if type(pos) ~= "table" then error("util.ScreenShake needs a position") end
			S.shakes = (S.shakes or 0) + 1
		end,
		GetConVarNumber = function(n) local c = S.convars[n] return c and c:GetFloat() or 0 end,
		IsOfAllowedType = function() return true end
	}

	-- Garry's Mod default (engine) effects, from the wiki's list. util.Effect with a
	-- name outside this and outside the addon's own Lua effects is a typo.
	ENGINE_EFFECTS = {
		ManhackSparks = true, VortDispel = true, ThumperDust = true, StunstickImpact = true,
		CommandPointer = true, MuzzleFlash = true, AR2Impact = true, AR2Explosion = true,
		AR2Tracer = true, GaussTracer = true, Impact = true, Impact_GMOD = true, GlassImpact = true,
		BoltImpact = true, TeslaZap = true, TeslaHitboxes = true, Smoke = true, cball_explode = true,
		cball_bounce = true, HelicopterImpact = true, HelicopterTracer = true, RPGShotDown = true,
		GunshipImpact = true, AntlionGib = true, StriderBlood = true, CrossbowLoad = true,
		PhyscannonImpact = true, StriderTracer = true, GunshipTracer = true, ImpactGauss = true,
		ImpactJeep = true, ImpactGunship = true, AirboatGunTracer = true, AirboatGunHeavyTracer = true,
		AirboatGunImpact = true, AirboatMuzzleFlash = true, ChopperMuzzleFlash = true,
		HunterMuzzleFlash = true, HunterTracer = true, HL1ShellEject = true, HL1Gib = true,
		explosion = true
	}

	g.particle = { CanHaveEffect = function() return true end }
	g.ParticleEffectExists = function(name)
		local known = { smoke = 1, ember = 1, teleport = 1, dust = 1, flame6 = 1,
			gunsmoke = 1, ['striderboom'] = 1 }
		return known[name] ~= nil
	end
	-- ParticleEffect( name, position, angle, parent = NULL ) - four args, the last
	-- one an ENTITY. Passing a count here is a classic mistake, so fail loudly.
	g.ParticleEffect = function(name, pos, ang, parent)
		if type(name) ~= "string" then error("ParticleEffect: name must be a string") end
		if type(pos) ~= "table" then error("ParticleEffect: position must be a Vector") end
		if ang ~= nil and type(ang) ~= "table" then error("ParticleEffect: angle must be an Angle") end
		if parent ~= nil and type(parent) ~= "table" then
			error("ParticleEffect: arg 4 is an optional parent Entity, not a count")
		end
		S.particleCalls = S.particleCalls + 1
	end
	g.ParticleEmitterAttach = function() S.particleCalls = S.particleCalls + 1 end

	g.CreateSound = function(ent, name)
		return {
			PlayEx = function(v, p) table.insert(S.soundCalls, { ent = "loop", sound = name, vol = v }) end,
			Stop = function() end,
			SetVolumeLevel = function() end,
			SetSoundLevel = function() end,
			DuckEnabled = false
		}
	end
	-- EmitSound( name, position, entity = 0, channel, volume, soundLevel, flags, pitch )
	g.EmitSound = function(name, pos, entity, channel, volume, level, flags, pitch)
		if type(pos) ~= "table" then error("EmitSound: arg 2 must be a position Vector") end
		if entity ~= nil and type(entity) ~= "number" then error("EmitSound: arg 3 is an entity index, got " .. type(entity)) end
		if channel ~= nil and type(channel) ~= "number" then error("EmitSound: arg 4 is the channel") end
		if volume ~= nil and (type(volume) ~= "number" or volume < 0 or volume > 1) then
			error("EmitSound: arg 5 is volume (0-1), got " .. tostring(volume) .. " - wrong argument order?")
		end
		if level ~= nil and (type(level) ~= "number" or level > 511) then error("EmitSound: arg 6 is soundLevel") end
		table.insert(S.soundCalls, { ent = "world", sound = name, vol = volume, level = level })
	end

	g.game = {
		GetWorld = function()
			return setmetatable({ index = 0, classname = "worldspawn", EmitSound = function() end }, entityBase)
		end,
		GetMap = function() return "gm_construct" end,
		SinglePlayer = true
	}

	g.LocalPlayer = function()
		if S.players and S.players[1] then return S.players[1] end
		return nil
	end
	g.GetViewEntity = g.LocalPlayer
	g.Entity = function(i) return S.ents[i] end
	g.Player = function(i) return S.ents[i] end
	g.EyePos = function() return S.eyePos end
	g.EyeAngles = function() return api.Angle(-12, 90, 0) end

	g.player = {
		GetAll = function() local t = S.players if t then return t end return {} end
	}

	g.ents = {
		Create = function(class)
			local e = api.newEntity(class)
			if not S.entitiesByClass[class] then
				e.isNPC = string.find(class, "^npc_") ~= nil
				e.health = 60
				S.createdNPCs = S.createdNPCs + 1
			end
			S.ents[e.index] = e
			if classFor(class).Initialize then
				local ok, err = pcall(function() e:Initialize() end)
				if not ok then error("ENT:Initialize failed for " .. class .. ": " .. tostring(err)) end
			end
			return e
		end,
		FindInSphere = function(pos, radius)
			local out = {}
			for _, pl in ipairs(S.players or {}) do out[#out + 1] = pl end
			-- a few victims and props to hit, so damage paths get exercised
			for i = 1, 3 do
				out[#out + 1] = api.newEntity("npc_zombie")
			end
			return out
		end,
		RemoveAll = function() end
	}

	g.DamageInfo = function()
		local d = {}
		return {
			SetAttacker = function(_, v) d.att = v end,
			SetInflictor = function(_, v) d.inf = v end,
			SetDamageType = function(_, v) d.type = v end,
			SetDamage = function(_, v) d.dmg = v end,
			SetDamagePosition = function(_, v) d.pos = v end,
			SetReportedPosition = function(_, v) d.rpos = v end,
			SetDamageForce = function(_, v) d.force = v end,
			Get = function(k) return d[k] end
		}
	end

	g.spawnmenu = {
		AddToolMenuOption = function(tab, category, class, name, cmd, config, fn, tbl)
			if type(tab) ~= "string" or type(category) ~= "string" or type(class) ~= "string" or type(name) ~= "string" then
				error("spawnmenu.AddToolMenuOption: (tab, category, class, name) must be strings")
			end
			if fn ~= nil and type(fn) ~= "function" then error("spawnmenu.AddToolMenuOption: cpanel must be a function") end
			S.toolMenu = fn
			S.toolMenuArgs = { tab = tab, category = category, class = class }
		end,
		AddToolCategory = function(tab, category, name)
			S.toolCategories = S.toolCategories or {}
			S.toolCategories[tab .. "/" .. category] = name
		end,
		AddCreationTab = function() end,
		GetEntityTypeIcon = function() return nil end
	}

	g.language = { Get = function(k) return k end, Add = function() end }

	g.list = { Get = function() return {} end, Add = function() end, Exists = function() return true end }
	g.AddCSLuaFile = function() end
	g.include = function(path)
		local full = S.addonRoot .. "/" .. path
		local chunk = assert(loadfile(full, "bt", g))
		return chunk()
	end
	g.AddCSLuaFile = function() end
	g.require = nil

	-- constants the addon reads
	local consts = {
		SNDCHAN_AUTO = 0, SNDCHAN_WEAPON = 1, SNDCHAN_STATIC = 6, SNDCHAN_VOICE = 2,
		SNDLVL_GUNFIRE = 100, SNDLVL_IDLE = 60, SNDLVL_STATIC = 66, SNDLVL_NORM = 66, SNDLVL_GTAV = 90,
		SOUNDING_STOP_ALL = 1,
		DMG_BULLET = 2, DAMAGE_BULLET = 2, DMG_CLUB = 1, DMG_SHOCK = 64, DMG_USEDARK = 0,
		MASK_SHOT = 34479, MASK_SHOT_HULL = 34479, MASK_VISIBLE = 16777216,
		MOVETYPE_NONE = 0, MOVETYPE_VPHYSICS = 9, MOVETYPE_WALK = 3,
		SOLID_NONE = 0, SOLID_BBOX = 2, SOLID_VPHYSICS = 7,
		COLLISION_GROUP_DEBRIS = 6, COLLISION_GROUP_DEBRIS_TRIGGER = 7, COLLISION_GROUP_INTERACTIVE = 1,
		OVERRIDE_USEALL = 2,
		FCVAR_ARCHIVE = 1, FCVAR_REPLICATED = 32, FCVAR_NOTIFY = 4, FCVAR_USERINFO = 2,
		TEXT_ALIGN_LEFT = 0, TEXT_ALIGN_CENTER = 1, TEXT_ALIGN_RIGHT = 2, TEXT_ALIGN_TOP = 3, TEXT_ALIGN_BOTTOM = 4,
		ACT_VM_DRAW = 1, ACT_VM_HITCENTER = 2, ACT_VM_MISSCENTER = 3,
		SCHED_CHASE_ENEMY = 1002,
		RENDERGROUP_NONE = 0, RENDERGROUP_OPAQUE = 1, RENDERGROUP_BOTH = 3,
		PAINT_TOP = 2,
		MATERIAL_QUADS = 3,
		PARTICLE_ATTACH = 1
	}
	for k, v in pairs(consts) do g[k] = v end

	-- the parts of standard Lua that GMod also just has lying around
	for _, name in ipairs({
		"pairs", "ipairs", "next", "select", "type", "pcall", "xpcall", "error", "assert",
		"setmetatable", "getmetatable", "rawget", "rawset", "rawequal", "unpack", "print",
		"tonumber", "tostring", "math", "table", "string", "io", "os", "coroutine", "debug"
	}) do
		if g[name] == nil then g[name] = _G[name] end
	end

	g._G = g
end

-- ----------------------------------------------------------------- driving
function api.loadEntityScripts(root)
	S.metatables = {}
	for _, name in ipairs({ "epic_zombie_breach", "epic_missile_barrage" }) do
		local path = root .. "/lua/entities/" .. name .. ".lua"
		local chunk = assert(loadfile(path, "bt", g))
		-- GMod hands each entity script a fresh ENT table; that is our ENT, and the
		-- instance metatable falls back to base_anim (entityBase) just like the game
		local ENT = {}
		g.ENT = ENT
		chunk()
		g.ENT = nil
		S.entitiesByClass[name] = ENT
	end
end

function api.loadEffectScripts(root)
	for _, name in ipairs({ "epic_sweps_fx" }) do
		local chunk = assert(loadfile(root .. "/lua/effects/" .. name .. ".lua", "bt", g))
		chunk()
	end
end

function api.loadWeapon(root, class)
	local chunk = assert(loadfile(root .. "/lua/weapons/" .. class .. "/shared.lua", "bt", g))
	-- SWEP files reference SWEP as a global, exactly like GMod does
	local SWEP = setmetatable({}, { __newindex = function(t, k, v) rawset(t, k, v) end, __index = function(t, k) return SWEP_BASE[k] end })
	local old = g.SWEP
	g.SWEP = SWEP
	chunk()
	g.SWEP = old
	local inst = setmetatable({ class = class }, { __index = SWEP })
	return inst
end

function api.step(dt)
	S.time = S.time + dt

	-- timers
	local due = {}
	for name, t in pairs(S.timers) do
		if t.next and S.time >= t.next then due[#due + 1] = { name = name, t = t } end
	end
	for _, d in ipairs(due) do
		d.t.fn()
		if d.t.once then
			S.timers[d.name] = nil
		else
			d.t.next = S.time + d.t.period
		end
	end

	-- entities (only the addon's own classes; the fakes have no behaviour)
	for idx, ent in pairs(S.ents) do
		local ENT = type(ent) == "table" and S.entitiesByClass[ent.classname]
		if ENT and ENT.Think and (ent.nextThink == nil or S.time >= ent.nextThink) then
			ent.nextThink = nil
			local ok, err = pcall(function() ENT.Think(ent) end)
			if not ok then error("ENT:Think failed for " .. tostring(ent.classname) .. ": " .. tostring(err)) end
		end
	end
end

function api.runHook(name, a, b, c)
	if not S.hooks[name] then return end
	for _, fn in pairs(S.hooks[name]) do
		local ok, err = pcall(fn, a, b, c)
		if not ok then error("hook " .. name .. " failed: " .. tostring(err)) end
	end
end

function api.replayNet()
	for _, msg in ipairs(S.netlog) do
		S.netIn = {}
		for i = 1, #msg.vals do S.netIn[i] = msg.vals[i] end
		local fn = S.netRecv[msg.str]
		if fn then
			local ok, err = pcall(fn, 64)
			if not ok then error("net.Receive failed: " .. tostring(err)) end
		end
	end
end

function api.stateReset() setup() end

api.entityBase = entityBase
api.S = S
api.getG = function() return g end
api.setRealm = function(r) S.realm = r end
api.setRoot = function(r) S.addonRoot = r end
api.countDraw = function(k) return S.drawCalls[k] or 0 end
api.count = function(k) return S.drawCalls[k] or 0 end

return api
