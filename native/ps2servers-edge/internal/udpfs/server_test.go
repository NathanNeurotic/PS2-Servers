package udpfs

import (
	"encoding/json"
	"os"
	"testing"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
)

func TestClassify(t *testing.T) {
	cases := []struct {
		d, f uint16
		want session.Profile
	}{{0, 0, session.Standard}, {0, 1, session.Modulo}, {7, 8, session.Modulo}, {4095, 0, session.Modulo}, {7, 0, session.Standard}}
	for _, tc := range cases {
		if got := Classify(tc.d, tc.f); got != tc.want {
			t.Fatalf("discovery=%d first=%d got %s want %s", tc.d, tc.f, got, tc.want)
		}
	}
}

func TestSharedHandshakeFixtures(t *testing.T) {
	type fixture struct {
		Name      string          `json:"name"`
		Discovery uint16          `json:"discovery"`
		First     uint16          `json:"first_data"`
		Profile   session.Profile `json:"profile"`
	}
	path := "../../../../conformance/fixtures/handshake_cases.json"
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var cases []fixture
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			if got := Classify(tc.Discovery, tc.First); got != tc.Profile {
				t.Fatalf("got %s want %s", got, tc.Profile)
			}
		})
	}
}
