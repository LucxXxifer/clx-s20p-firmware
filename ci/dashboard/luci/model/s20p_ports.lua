-- Read-only compatibility for the known USB netdev's IFLA_OPERSTATE=UNKNOWN.
-- UP here means device link only; it says nothing about Internet reachability.
local M={}
local function copy(value)
  if type(value)~="table" then return value end
  local result={}
  for key,item in pairs(value)do result[key]=copy(item)end
  return result
end
local function target(port)
  if type(port)~="table" or port.name~="eth2" or port.linkState~="UNKNOWN" or
    type(port.interfaceNames)~="table" then return false end
  for _,name in ipairs(port.interfaceNames)do
    if name=="usb_uplink" then return true end
  end
  return false
end
function M.transform(kind,body,env)
  if kind~="ports" then return nil,"unsupported USB port endpoint" end
  local value=env.decode(body)
  if type(value)~="table" or value.error~=nil or
    (value.success~=nil and value.success~=true and value.success~=200 and value.success~=0) or
    type(value.result)~="table" or type(value.result.ports)~="table" then return body end
  local adapted
  for index,port in ipairs(value.result.ports)do
    if target(port)then
      local ok,state=pcall(env.device,port.name)
      if ok and type(state)=="table" and state.present==true and state.up==true and state.carrier==true then
        adapted=adapted or copy(value)
        adapted.result.ports[index].linkState="UP"
      end
    end
  end
  return adapted and env.encode(adapted) or body
end
function M.production_env()
  local json=require "luci.jsonc"
  local util=require "luci.util"
  return {decode=json.parse,encode=json.stringify,
    device=function(name)return util.ubus("network.device","status",{name=name})end}
end
return M
