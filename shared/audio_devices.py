"""The PC's audio devices by name, for the GUI's device pickers and the instructor's speaker output.

Names come from one host API that gives full names and plays any sample rate: DirectSound on Windows (MME cuts names
at 31 characters; WASAPI shared mode refuses rates other than the device's), the default API elsewhere (ALSA on the
Raspberry Pi). Settings store the name; '' means the system default device.

windows_endpoints() reads the Windows audio endpoints (id + name) from the registry: SRS stores its devices by endpoint
id (Client/global.cfg), so this is how the GUI shows which device SRS plays on.
"""
import sys

PREFERRED_API = 'Windows DirectSound'
ENDPOINT_KEY = r'SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio'
ENDPOINT_PREFIX = {'Render': '{0.0.0.00000000}.', 'Capture': '{0.0.1.00000000}.'}
NAME_PROPERTY = '{a45c254e-df1c-4efd-8020-67d146a850e0},2'           # "Headphones"
INTERFACE_PROPERTY = '{b3f8fa53-0004-438e-9003-51a46e139bfc},6'      # "Wireless Gaming Headset"
DEVICE_STATE_ACTIVE = 1


def _host_api(apis: list[dict]) -> int:
    for index, api in enumerate(apis):
        if api['name'] == PREFERRED_API:
            return index
    import sounddevice as sd
    return sd.default.hostapi


def list_devices(kind: str, devices=None, apis=None) -> list[str]:
    """Device names of kind 'input' or 'output', in the system's order, without the API's own "default device"
    alias (DirectSound's "Primary Sound Driver" is not a device). devices/apis: sounddevice's query results."""
    if devices is None or apis is None:
        import sounddevice as sd
        devices, apis = sd.query_devices(), sd.query_hostapis()
    api_index = _host_api(apis)
    api = apis[api_index]
    channels = 'max_input_channels' if kind == 'input' else 'max_output_channels'
    alias = api.get('default_input_device' if kind == 'input' else 'default_output_device')
    alias = alias if api['name'] == PREFERRED_API else None
    names = []
    for index, device in enumerate(devices):
        if device['hostapi'] == api_index and device[channels] > 0 and index != alias and device['name'] not in names:
            names.append(device['name'])
    return names


def output_index(name: str) -> int | None:
    """sounddevice index of the named output device (exact name, else contained); None = system default."""
    if not name:
        return None
    import sounddevice as sd
    devices, apis = sd.query_devices(), sd.query_hostapis()
    api_index = _host_api(apis)
    candidates = [(i, d['name']) for i, d in enumerate(devices)
                  if d['hostapi'] == api_index and d['max_output_channels'] > 0]
    wanted = name.lower()
    for i, device_name in candidates:
        if device_name.lower() == wanted:
            return i
    for i, device_name in candidates:
        if wanted in device_name.lower():
            return i
    raise LookupError(f'output device "{name}" not found; outputs: {[n for _, n in candidates]}')


def windows_endpoints(flow: str = 'Render') -> dict[str, str]:
    """Active Windows audio endpoints of a flow ('Render' = outputs, 'Capture' = inputs): endpoint id -> name as
    Windows shows it, "Headphones (Wireless Gaming Headset)". Empty outside Windows."""
    if sys.platform != 'win32':
        return {}
    import winreg
    endpoints = {}
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf'{ENDPOINT_KEY}\{flow}')
    except OSError:
        return {}
    with root:
        index = 0
        while True:
            try:
                guid = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(root, guid) as key:
                    if winreg.QueryValueEx(key, 'DeviceState')[0] != DEVICE_STATE_ACTIVE:
                        continue
                with winreg.OpenKey(root, rf'{guid}\Properties') as key:
                    name = winreg.QueryValueEx(key, NAME_PROPERTY)[0]
                    interface = winreg.QueryValueEx(key, INTERFACE_PROPERTY)[0]
            except OSError:
                continue
            endpoints[ENDPOINT_PREFIX[flow] + guid] = f'{name} ({interface})'
    return endpoints
