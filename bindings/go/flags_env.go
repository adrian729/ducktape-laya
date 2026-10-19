//go:build !pkgconfig

package laya

// Header and library locations come from CGO_CFLAGS / CGO_LDFLAGS (see README).

/*
#cgo LDFLAGS: -lducktape_laya
*/
import "C"
