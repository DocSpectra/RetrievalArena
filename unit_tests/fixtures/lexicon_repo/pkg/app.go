package pkg

import "fmt"

// Application is the CRD.
type Application struct{}

// SyncPolicy is exported.
type SyncPolicy struct{}

func (a *Application) Name() string { return "app" }

func helper() {}

func init() { Foo = "bar" }

const AnnotationHookDeletePolicy = "argocd.argoproj.io/hook-delete-policy"

var Use = "app"

var (
	DefaultRevision = "HEAD"
	lower           = 1
)

func main() { fmt.Println("hello world") }
