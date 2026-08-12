package webui

import (
	"encoding/json"
	"io"
)

// The machine-readable form of /etc/config/ps2servers-edge, printed by
// `ps2servers-edge --dump-schema`.
//
// It exists for firmware/tooling that GENERATES the UCI config (router images,
// provisioning adapters): they validate against the exact binary they ship
// instead of a hardcoded copy of this list. The stability rule is that section
// and option names are additive-only -- a new field may appear, an existing one
// is never renamed or removed -- so schema_version only bumps on additions.
//
// Defaults and coverage are not hand-maintained here in spirit: schema_test.go
// walks DefaultConfig() and fails if a field is missing, mis-typed, or has a
// different default, and probes ValidateConfig against every constraint marked
// enforced by the API. Editing the config model without updating this table
// breaks the build, which is the point.

// Field types emitted in "type".
const (
	TypeBool     = "bool"
	TypeInt      = "int"
	TypeFloat    = "float"
	TypeString   = "string"
	TypePath     = "path"
	TypeDuration = "duration" // Go duration ("1h") or plain seconds
	TypeEnum     = "enum"
)

// Who rejects a bad value first. "webui-api" means ValidateConfig refuses it
// (so the dashboard's Save also reports it); "server" means only the server
// refuses at start. Fields without constraints omit the key.
const (
	EnforcedByAPI    = "webui-api"
	EnforcedByServer = "server"
)

// schema_version history (the additive-only rule: consumers must keep working
// when a new version appears):
//
//	1 -- initial
//	2 -- FieldSpec gained "applies_when": the name of a sibling bool option
//	     that must be enabled before the field's constraints (and value)
//	     matter at all. metrics_period is the first user: the server parses
//	     it only when metrics is on, so an unconditional range would have a
//	     validator reject disabled-metrics configs the binary accepts.
const schemaVersion = 2

type FieldSpec struct {
	Name       string   `json:"name"`
	Type       string   `json:"type"`
	Default    any      `json:"default"`
	Doc        string   `json:"doc,omitempty"`
	Values     []string `json:"values,omitempty"`      // enum: the accepted values
	Min        *float64 `json:"min,omitempty"`         // numeric lower bound
	Max        *float64 `json:"max,omitempty"`         // numeric upper bound
	MultipleOf *int     `json:"multiple_of,omitempty"` // int: value must be a multiple of this
	MinValue   string   `json:"min_value,omitempty"`   // duration lower bound
	MaxValue   string   `json:"max_value,omitempty"`   // duration upper bound
	EnforcedBy string   `json:"enforced_by,omitempty"`
	// A sibling bool option that gates this field's constraints. Empty means
	// unconditional.
	AppliesWhen string `json:"applies_when,omitempty"`
}

type SectionSpec struct {
	Name string `json:"name"` // EdgeConfig section name
	// The UCI section as `uci set ps2servers-edge.<uci_section>.<option>`.
	// udpfs is the historical 'main'; the rest match their name.
	UCISection  string      `json:"uci_section"`
	Description string      `json:"description,omitempty"`
	Fields      []FieldSpec `json:"fields"`
}

type Schema struct {
	SchemaVersion int           `json:"schema_version"`
	Binary        string        `json:"binary"`
	Version       string        `json:"version"`
	ConfigFile    string        `json:"config_file"`
	Sections      []SectionSpec `json:"sections"`
}

func fnum(v float64) *float64 { return &v }
func inum(v int) *int         { return &v }

// schema assembles the contract. Defaults for fields modelled in EdgeConfig
// must equal DefaultConfig() -- the test walks both.
func schema(version string) *Schema {
	return &Schema{
		SchemaVersion: schemaVersion,
		Binary:        "ps2servers-edge",
		Version:       version,
		ConfigFile:    "/etc/config/ps2servers-edge",
		Sections: []SectionSpec{
			{
				Name:       "udpfs",
				UCISection: "main",
				Description: "Network file share over UDP, with optional disk-image " +
					"block access. The recommended mode for most setups.",
				Fields: []FieldSpec{
					{Name: "enabled", Type: TypeBool, Default: true, Doc: "start this service at boot"},
					{Name: "root", Type: TypePath, Default: "/mnt/sda1/PS2", Doc: "directory served to consoles; required to start"},
					{Name: "bind", Type: TypeString, Default: "0.0.0.0", Doc: "IPv4 address to listen on"},
					{Name: "port", Type: TypeInt, Default: 62966, Min: fnum(0), Max: fnum(65535), EnforcedBy: EnforcedByAPI, Doc: "UDP discovery port (0xF5F6)"},
					{Name: "data_port", Type: TypeInt, Default: 0, Min: fnum(0), Max: fnum(65535), EnforcedBy: EnforcedByAPI, Doc: "fixed UDP data port; 0 chooses automatically"},
					{Name: "protocol", Type: TypeEnum, Default: "auto", Values: []string{"auto", "standard", "modulo"}, EnforcedBy: EnforcedByAPI, Doc: "auto classifies each client on its own; force one only when auto misjudges a console"},
					{Name: "single_port", Type: TypeBool, Default: false, Doc: "use the discovery port for all traffic"},
					{Name: "read_only", Type: TypeBool, Default: true, Doc: "refuse writes. The package ships 1 (a router share is often a whole disk); the binary itself defaults to writable"},
					{Name: "peer_timeout", Type: TypeDuration, Default: "1h", MinValue: "1m", MaxValue: "24h", EnforcedBy: EnforcedByServer, Doc: "idle session expiry; Go duration or plain seconds"},
					{Name: "tx_delay_ms", Type: TypeFloat, Default: float64(0), Min: fnum(0), EnforcedBy: EnforcedByAPI, Doc: "inter-packet pacing in milliseconds; raise it if a slow link drops packets"},
					{Name: "log_format", Type: TypeEnum, Default: "text", Values: []string{"text", "json"}, EnforcedBy: EnforcedByAPI},
					{Name: "verbose", Type: TypeBool, Default: false, Doc: "verbose protocol logging"},
					{Name: "block_device", Type: TypePath, Default: "", Doc: "also serve this disk image over UDPFS block access; empty disables. Inherits read_only"},
					{Name: "sector_size", Type: TypeInt, Default: 512, Min: fnum(512), Max: fnum(65536), MultipleOf: inum(512), EnforcedBy: EnforcedByServer, Doc: "sector size for block_device; only sent alongside it"},
					{Name: "no_compression", Type: TypeBool, Default: false, Doc: "serve CSO/ZSO as raw bytes. Diagnostic only -- games do not load this way"},
					{Name: "compression_cache_size", Type: TypeInt, Default: 32, Min: fnum(0), Max: fnum(4096), EnforcedBy: EnforcedByServer, Doc: "decompressed blocks cached per open image; 0 disables"},
					{Name: "metrics", Type: TypeBool, Default: false, Doc: "log periodic transfer counters to syslog"},
					{Name: "metrics_period", Type: TypeDuration, Default: "1m", MinValue: "1s", MaxValue: "24h", EnforcedBy: EnforcedByServer, AppliesWhen: "metrics", Doc: "how often metrics are logged; only used when metrics is 1"},
				},
			},
			{
				Name:       "smb",
				UCISection: "smb",
				Description: "SMBv1 shares for OPL's network game list and POPSTARTER. " +
					"Off by default: enabling binds a port with no password.",
				Fields: []FieldSpec{
					{Name: "enabled", Type: TypeBool, Default: false, Doc: "start this service at boot"},
					{Name: "share", Type: TypeString, Default: "games=/mnt/sda1/PS2", Doc: "space-separated NAME=PATH pairs. Required to start; a path containing a space cannot be expressed"},
					{Name: "bind", Type: TypeString, Default: "0.0.0.0", Doc: "IPv4 address to listen on"},
					{Name: "port", Type: TypeInt, Default: 1111, Min: fnum(0), Max: fnum(65535), EnforcedBy: EnforcedByAPI, Doc: "TCP port; set OPL's SMB Port to match. Below 1024 needs root"},
					{Name: "read_only", Type: TypeBool, Default: true, Doc: "refuse writes; this server has no password"},
					{Name: "status_port", Type: TypeInt, Default: 0, Min: fnum(-1), Max: fnum(65535), EnforcedBy: EnforcedByAPI, Doc: "router status query port: 0 is off, -1 is the standard discovery port (which udpfs already owns -- set -1 only when smb is the only service)"},
					{Name: "log_format", Type: TypeEnum, Default: "text", Values: []string{"text", "json"}, EnforcedBy: EnforcedByAPI},
					{Name: "verbose", Type: TypeBool, Default: false, Doc: "verbose protocol logging"},
				},
			},
			{
				Name:       "udpbd",
				UCISection: "udpbd",
				Description: "One disk image as a network hard drive. A separate " +
					"protocol from udpfs on its own port; largely superseded by udpfs.",
				Fields: []FieldSpec{
					{Name: "enabled", Type: TypeBool, Default: false, Doc: "start this service at boot"},
					{Name: "image", Type: TypePath, Default: "", Doc: "disk image to serve; required to start"},
					{Name: "bind", Type: TypeString, Default: "0.0.0.0", Doc: "IPv4 address to listen on"},
					{Name: "port", Type: TypeInt, Default: 48573, Min: fnum(0), Max: fnum(65535), EnforcedBy: EnforcedByAPI, Doc: "0xBDBD -- the port the PS2's udpbd client broadcasts to, not a free choice"},
					{Name: "read_only", Type: TypeBool, Default: true, Doc: "refuse writes"},
					{Name: "status_port", Type: TypeInt, Default: 0, Min: fnum(-1), Max: fnum(65535), EnforcedBy: EnforcedByAPI, Doc: "router status query port: 0 is off, -1 is the standard discovery port (set -1 only when udpbd is the only service)"},
					{Name: "log_format", Type: TypeEnum, Default: "text", Values: []string{"text", "json"}, EnforcedBy: EnforcedByAPI},
					{Name: "verbose", Type: TypeBool, Default: false, Doc: "verbose protocol logging"},
				},
			},
			{
				Name:       "webui",
				UCISection: "webui",
				Description: "Web dashboard. Reaching it is equivalent to shell " +
					"access: it rewrites this config and restarts services.",
				Fields: []FieldSpec{
					{Name: "enabled", Type: TypeBool, Default: false, Doc: "start this service at boot"},
					{Name: "port", Type: TypeInt, Default: 8082, Min: fnum(0), Max: fnum(65535), EnforcedBy: EnforcedByAPI, Doc: "HTTP port; 0 uses 8082 with fallback to an OS-assigned port"},
					{Name: "bind", Type: TypeString, Default: "0.0.0.0", Doc: "a non-loopback bind needs auth_pass or insecure -- the init script refuses to start it otherwise"},
					// auth_* and the two toggles below are consumed by the init
					// script (mapped to CLI flags and WEBUI_PASS), not modelled in
					// EdgeConfig: the dashboard gets its credentials at process
					// start, not from reading this file back.
					{Name: "auth_user", Type: TypeString, Default: "admin", Doc: "HTTP basic auth username"},
					{Name: "auth_pass", Type: TypeString, Default: "", Doc: "HTTP basic auth password. Required for a non-loopback bind; passed to the process as WEBUI_PASS, never on the command line"},
					{Name: "insecure", Type: TypeBool, Default: false, Doc: "accept a LAN bind with no password; a random one is generated and logged at every start"},
					{Name: "run_as_root", Type: TypeBool, Default: false, Doc: "let the dashboard's Save and Restart work (uci commit + init.d restart are root's). At 0 the dashboard is read-only: status, config view and file browsing all work"},
				},
			},
		},
	}
}

// WriteSchema prints the schema as indented JSON for `ps2servers-edge
// --dump-schema`.
func WriteSchema(w io.Writer, version string) error {
	out, err := json.MarshalIndent(schema(version), "", "  ")
	if err != nil {
		return err
	}
	_, err = w.Write(append(out, '\n'))
	return err
}
