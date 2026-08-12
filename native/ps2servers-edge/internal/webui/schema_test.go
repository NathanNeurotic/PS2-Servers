package webui

import (
	"bytes"
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

// The schema table is what tooling validates against, so it must not drift
// from what the binary actually does. These tests pin it to the two things
// that already enforce the config for real: DefaultConfig() (names, types,
// defaults) and ValidateConfig (constraints marked enforced by the API).

func jsonTagName(sf reflect.StructField) string {
	return strings.Split(sf.Tag.Get("json"), ",")[0]
}

func sectionValue(cfg *EdgeConfig, section string) reflect.Value {
	v := reflect.ValueOf(cfg).Elem()
	t := v.Type()
	for i := 0; i < t.NumField(); i++ {
		if jsonTagName(t.Field(i)) == section {
			return v.Field(i)
		}
	}
	return reflect.Value{}
}

func setConfigValue(t *testing.T, cfg *EdgeConfig, key string, value any) {
	t.Helper()
	parts := strings.SplitN(key, ".", 2)
	sv := sectionValue(cfg, parts[0])
	if !sv.IsValid() {
		t.Fatalf("no config section %q", parts[0])
	}
	st := sv.Type()
	for i := 0; i < st.NumField(); i++ {
		if jsonTagName(st.Field(i)) != parts[1] {
			continue
		}
		fv := sv.Field(i)
		rv := reflect.ValueOf(value)
		if !rv.Type().ConvertibleTo(fv.Type()) {
			t.Fatalf("%s: cannot assign %T to %s", key, value, fv.Type())
		}
		fv.Set(rv.Convert(fv.Type()))
		return
	}
	t.Fatalf("no config field %q", key)
}

func configKeys(cfg *EdgeConfig) map[string]bool {
	keys := map[string]bool{}
	v := reflect.ValueOf(cfg).Elem()
	t := v.Type()
	for i := 0; i < t.NumField(); i++ {
		sec := jsonTagName(t.Field(i))
		ft := v.Field(i).Type()
		for j := 0; j < ft.NumField(); j++ {
			keys[sec+"."+jsonTagName(ft.Field(j))] = true
		}
	}
	return keys
}

func TestSchemaMatchesDefaultConfig(t *testing.T) {
	s := schema("test")
	bySection := map[string]SectionSpec{}
	for _, sec := range s.Sections {
		bySection[sec.Name] = sec
	}
	cfg := DefaultConfig()
	cv := reflect.ValueOf(cfg).Elem()
	ct := cv.Type()
	for i := 0; i < ct.NumField(); i++ {
		secName := jsonTagName(ct.Field(i))
		sec, ok := bySection[secName]
		if !ok {
			t.Errorf("schema has no section %q", secName)
			continue
		}
		fields := map[string]FieldSpec{}
		for _, f := range sec.Fields {
			fields[f.Name] = f
		}
		fv, ft := cv.Field(i), cv.Field(i).Type()
		for j := 0; j < ft.NumField(); j++ {
			name := jsonTagName(ft.Field(j))
			spec, ok := fields[name]
			if !ok {
				t.Errorf("%s.%s: missing from the schema", secName, name)
				continue
			}
			if want := fv.Field(j).Interface(); !reflect.DeepEqual(spec.Default, want) {
				t.Errorf("%s.%s: schema default %#v, DefaultConfig() has %#v",
					secName, name, spec.Default, want)
			}
			switch fv.Field(j).Kind() {
			case reflect.Bool:
				if spec.Type != TypeBool {
					t.Errorf("%s.%s: schema type %q, config is bool", secName, name, spec.Type)
				}
			case reflect.Int:
				if spec.Type != TypeInt {
					t.Errorf("%s.%s: schema type %q, config is int", secName, name, spec.Type)
				}
			case reflect.Float64:
				if spec.Type != TypeFloat {
					t.Errorf("%s.%s: schema type %q, config is float", secName, name, spec.Type)
				}
			case reflect.String:
				switch spec.Type {
				case TypeString, TypePath, TypeDuration, TypeEnum:
				default:
					t.Errorf("%s.%s: schema type %q, config is string", secName, name, spec.Type)
				}
			}
		}
	}

	// Reverse direction: a schema field that EdgeConfig does not model must be
	// one of the known init-script-consumed webui options, or it is a typo.
	extras := map[string]bool{
		"webui.auth_user": true, "webui.auth_pass": true,
		"webui.insecure": true, "webui.run_as_root": true,
	}
	modelled := configKeys(cfg)
	for _, sec := range s.Sections {
		for _, f := range sec.Fields {
			key := sec.Name + "." + f.Name
			if !modelled[key] && !extras[key] {
				t.Errorf("%s is in the schema but not in EdgeConfig, and is not a known extra", key)
			}
		}
	}
}

func TestSchemaUCISectionNames(t *testing.T) {
	// The uci set path is ps2servers-edge.<uci_section>.<option>; the OpenWrt
	// init script loads sections by these names, so they are contract.
	want := map[string]string{"udpfs": "main", "smb": "smb", "udpbd": "udpbd", "webui": "webui"}
	got := map[string]string{}
	for _, sec := range schema("test").Sections {
		got[sec.Name] = sec.UCISection
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("uci section names: got %v, want %v", got, want)
	}
}

func TestSchemaConstraintsAreEnforcedByTheAPI(t *testing.T) {
	for _, sec := range schema("test").Sections {
		for _, f := range sec.Fields {
			if f.EnforcedBy != EnforcedByAPI {
				continue
			}
			key := sec.Name + "." + f.Name
			if len(f.Values) > 0 {
				cfg := DefaultConfig()
				setConfigValue(t, cfg, key, "not-a-valid-value")
				if err := ValidateConfig(cfg); err == nil {
					t.Errorf("%s: ValidateConfig accepted an invalid enum value", key)
				}
				for _, v := range f.Values {
					cfg := DefaultConfig()
					setConfigValue(t, cfg, key, v)
					if err := ValidateConfig(cfg); err != nil {
						t.Errorf("%s: ValidateConfig rejected valid value %q: %v", key, v, err)
					}
				}
			}
			if f.Min != nil {
				cfg := DefaultConfig()
				setConfigValue(t, cfg, key, *f.Min-1)
				if err := ValidateConfig(cfg); err == nil {
					t.Errorf("%s: ValidateConfig accepted %v, below min %v", key, *f.Min-1, *f.Min)
				}
			}
			if f.Max != nil {
				cfg := DefaultConfig()
				setConfigValue(t, cfg, key, *f.Max+1)
				if err := ValidateConfig(cfg); err == nil {
					t.Errorf("%s: ValidateConfig accepted %v, above max %v", key, *f.Max+1, *f.Max)
				}
			}
		}
	}
}

func TestMetricsPeriodIsConditionalOnMetrics(t *testing.T) {
	// The binary parses metrics_period only when metrics is on; an
	// unconditional range in the schema would have a validator reject
	// disabled-metrics configs the binary accepts.
	for _, sec := range schema("test").Sections {
		if sec.Name != "udpfs" {
			continue
		}
		for _, f := range sec.Fields {
			if f.Name == "metrics_period" {
				if f.AppliesWhen != "metrics" {
					t.Errorf("metrics_period applies_when: got %q, want %q", f.AppliesWhen, "metrics")
				}
				return
			}
		}
	}
	t.Error("udpfs.metrics_period is missing from the schema")
}

func TestSchemaAppliesWhenReferencesARealBoolSibling(t *testing.T) {
	for _, sec := range schema("test").Sections {
		bools := map[string]bool{}
		for _, f := range sec.Fields {
			if f.Type == TypeBool {
				bools[f.Name] = true
			}
		}
		for _, f := range sec.Fields {
			if f.AppliesWhen != "" && !bools[f.AppliesWhen] {
				t.Errorf("%s.%s: applies_when %q is not a bool option in this section",
					sec.Name, f.Name, f.AppliesWhen)
			}
		}
	}
}

func TestWriteSchemaProducesValidJSON(t *testing.T) {
	var buf bytes.Buffer
	if err := WriteSchema(&buf, "v9.9.9-test"); err != nil {
		t.Fatal(err)
	}
	var doc map[string]any
	if err := json.Unmarshal(buf.Bytes(), &doc); err != nil {
		t.Fatalf("--dump-schema produced invalid JSON: %v", err)
	}
	if doc["schema_version"] != float64(schemaVersion) {
		t.Errorf("schema_version: got %v", doc["schema_version"])
	}
	if doc["binary"] != "ps2servers-edge" {
		t.Errorf("binary: got %v", doc["binary"])
	}
	if doc["version"] != "v9.9.9-test" {
		t.Errorf("version did not pass through: got %v", doc["version"])
	}
	if doc["config_file"] != "/etc/config/ps2servers-edge" {
		t.Errorf("config_file: got %v", doc["config_file"])
	}
	sections, ok := doc["sections"].([]any)
	if !ok || len(sections) != 4 {
		t.Fatalf("sections: got %v", doc["sections"])
	}
}
