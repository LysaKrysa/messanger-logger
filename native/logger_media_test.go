package bridge

import (
	"bytes"
	"testing"

	"go.mau.fi/mautrix-meta/pkg/messagix/table"
	"go.mau.fi/whatsmeow/proto/waArmadilloApplication"
	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waConsumerApplication"
	"go.mau.fi/whatsmeow/proto/waMediaTransport"
	"google.golang.org/protobuf/proto"
)

func loggerTestTransport() *waMediaTransport.WAMediaTransport {
	return &waMediaTransport.WAMediaTransport{
		Ancillary: &waMediaTransport.WAMediaTransport_Ancillary{Mimetype: proto.String("image/png"), FileLength: proto.Uint64(123)},
		Integral:  &waMediaTransport.WAMediaTransport_Integral{MediaKey: bytes.Repeat([]byte{1}, 32), FileSHA256: bytes.Repeat([]byte{2}, 32), FileEncSHA256: bytes.Repeat([]byte{3}, 32), DirectPath: proto.String("/synthetic/media")},
	}
}

func loggerTestImage() *waMediaTransport.ImageTransport {
	return &waMediaTransport.ImageTransport{
		Integral:  &waMediaTransport.ImageTransport_Integral{Transport: loggerTestTransport()},
		Ancillary: &waMediaTransport.ImageTransport_Ancillary{Width: proto.Uint32(640), Height: proto.Uint32(480)},
	}
}

func checkLoggerMedia(t *testing.T, att *Attachment, kind string) {
	t.Helper()
	if att == nil || att.Type != kind || !att.ViewOnce || att.DirectPath != "/synthetic/media" || len(att.MediaKey) != 32 || att.FileSize != 123 || att.Width != 640 {
		t.Fatalf("expected complete %s view-once attachment, got %#v", kind, att)
	}
}

func loggerConsumer(content *waConsumerApplication.ConsumerApplication_Content) *waConsumerApplication.ConsumerApplication {
	return &waConsumerApplication.ConsumerApplication{Payload: &waConsumerApplication.ConsumerApplication_Payload{Payload: &waConsumerApplication.ConsumerApplication_Payload_Content{Content: content}}}
}

func loggerArmadillo(content *waArmadilloApplication.Armadillo_Content) *waArmadilloApplication.Armadillo {
	return &waArmadilloApplication.Armadillo{Payload: &waArmadilloApplication.Armadillo_Payload{Payload: &waArmadilloApplication.Armadillo_Payload_Content{Content: content}}}
}

func TestLoggerConsumerViewOnce(t *testing.T) {
	client := &Client{}
	image := &waConsumerApplication.ConsumerApplication_ImageMessage{Caption: &waCommon.MessageText{Text: proto.String("caption")}}
	if err := image.Set(loggerTestImage()); err != nil {
		t.Fatal(err)
	}
	ca := loggerConsumer(&waConsumerApplication.ConsumerApplication_Content{
		Content: &waConsumerApplication.ConsumerApplication_Content_ViewOnceMessage{ViewOnceMessage: &waConsumerApplication.ConsumerApplication_ViewOnceMessage{
			ViewOnceContent: &waConsumerApplication.ConsumerApplication_ViewOnceMessage_ImageMessage{ImageMessage: image},
		}},
	})
	att, caption, found := client.loggerSpecialMedia(ca)
	if !found || caption != "caption" {
		t.Fatal("view-once caption/wrapper was lost")
	}
	checkLoggerMedia(t, att, "image")
	video := &waConsumerApplication.ConsumerApplication_VideoMessage{}
	if err := video.Set(&waMediaTransport.VideoTransport{Integral: &waMediaTransport.VideoTransport_Integral{Transport: loggerTestTransport()}, Ancillary: &waMediaTransport.VideoTransport_Ancillary{Width: proto.Uint32(640), Seconds: proto.Uint32(7)}}); err != nil {
		t.Fatal(err)
	}
	ca.Payload.GetContent().Content = &waConsumerApplication.ConsumerApplication_Content_ViewOnceMessage{ViewOnceMessage: &waConsumerApplication.ConsumerApplication_ViewOnceMessage{ViewOnceContent: &waConsumerApplication.ConsumerApplication_ViewOnceMessage_VideoMessage{VideoMessage: video}}}
	att, _, found = client.loggerSpecialMedia(ca)
	if !found || att.Duration != 7 {
		t.Fatal("view-once video duration was lost")
	}
	checkLoggerMedia(t, att, "video")
}

func TestLoggerRavenFormats(t *testing.T) {
	client := &Client{}
	for _, messenger := range []bool{true, false} {
		for _, video := range []bool{false, true} {
			raven := &waArmadilloApplication.Armadillo_Content_RavenMessage{EphemeralType: waArmadilloApplication.Armadillo_Content_RavenMessage_VIEW_ONCE.Enum()}
			kind := "image"
			if video {
				kind = "video"
				if err := raven.SetVideoMessage(&waMediaTransport.VideoTransport{Integral: &waMediaTransport.VideoTransport_Integral{Transport: loggerTestTransport()}, Ancillary: &waMediaTransport.VideoTransport_Ancillary{Width: proto.Uint32(640)}}); err != nil {
					t.Fatal(err)
				}
			} else if err := raven.SetImageMessage(loggerTestImage()); err != nil {
				t.Fatal(err)
			}
			content := &waArmadilloApplication.Armadillo_Content{}
			if messenger {
				content.Content = &waArmadilloApplication.Armadillo_Content_RavenMessageMsgr{RavenMessageMsgr: raven}
			} else {
				content.Content = &waArmadilloApplication.Armadillo_Content_RavenMessage_{RavenMessage: raven}
			}
			message := loggerArmadillo(content)
			att, _, found := client.loggerSpecialMedia(message)
			if !found {
				t.Fatal("Raven wrapper was not recognized")
			}
			checkLoggerMedia(t, att, kind)
		}
	}
}

func TestLoggerNilAndMalformedMedia(t *testing.T) {
	client := &Client{}
	for _, msg := range []any{nil, &waConsumerApplication.ConsumerApplication{}, &waArmadilloApplication.Armadillo{}} {
		if _, _, found := client.loggerSpecialMedia(msg); found {
			t.Fatal("unrelated message was treated as view-once")
		}
	}
	message := loggerArmadillo(&waArmadilloApplication.Armadillo_Content{Content: &waArmadilloApplication.Armadillo_Content_RavenMessageMsgr{RavenMessageMsgr: &waArmadilloApplication.Armadillo_Content_RavenMessage{MediaContent: &waArmadilloApplication.Armadillo_Content_RavenMessage_ImageMessage{ImageMessage: &waCommon.SubProtocol{Payload: []byte("invalid"), Version: proto.Int32(1)}}, EphemeralType: waArmadilloApplication.Armadillo_Content_RavenMessage_VIEW_ONCE.Enum()}}})
	att, _, found := client.loggerSpecialMedia(message)
	if !found || !att.ViewOnce || att.Type != "image" || len(att.MediaKey) != 0 || att.Description == "" {
		t.Fatal("malformed transport was not represented safely")
	}
}

func TestLoggerContactNamesAndInitialMetadata(t *testing.T) {
	tbl := &table.LSTable{LSDeleteThenInsertContact: []*table.LSDeleteThenInsertContact{{Id: 42, Name: "Alex", Username: "alex.real"}}, LSDeleteThenInsertThread: []*table.LSDeleteThenInsertThread{{ThreadKey: 42, ThreadName: "Friends"}}}
	contact := loggerContact(tbl, 42)
	if contact == nil || contact.Name != "Alex" || contact.Username != "alex.real" || loggerContact(tbl, 43) != nil {
		t.Fatal("contact lookup returned the wrong identity")
	}
	client := newEventPipelineTestClient(t, 0)
	client.emitLoggerContacts(tbl)
	event := <-client.eventChan
	if event.Type != "metadata" {
		t.Fatalf("contact metadata became %s", event.Type)
	}
	data := event.Data.(map[string]any)
	if len(data["contacts"].([]map[string]any)) != 1 || len(data["threads"].([]map[string]any)) != 1 {
		t.Fatal("initial metadata missing")
	}
}

func TestLoggerRegularEphemeralMedia(t *testing.T) {
	client := &Client{}
	att := client.convertBlobAttachment(&table.LSInsertBlobAttachment{AttachmentType: table.AttachmentTypeEphemeralImage, PreviewUrl: "https://fbcdn.net/test"})
	if att.Type != "image" || !att.ViewOnce || att.ViewMode != "limited" || att.URL == "" {
		t.Fatal("regular view-once image lost its media metadata")
	}
}
