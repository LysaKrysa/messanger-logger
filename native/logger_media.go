// Logger additions to the pinned fbchat-v2 bridge: receive-only media and names.
package bridge

import (
	"context"
	"fmt"
	"time"

	"go.mau.fi/mautrix-meta/pkg/messagix/socket"
	"go.mau.fi/mautrix-meta/pkg/messagix/table"
	"go.mau.fi/whatsmeow/proto/waArmadilloApplication"
	"go.mau.fi/whatsmeow/proto/waConsumerApplication"
	"go.mau.fi/whatsmeow/proto/waMediaTransport"
)

func loggerTransport(att *Attachment, transport *waMediaTransport.WAMediaTransport) {
	if transport == nil {
		return
	}
	if meta := transport.GetAncillary(); meta != nil {
		att.MimeType = meta.GetMimetype()
		att.FileSize = int64(meta.GetFileLength())
	}
	if integral := transport.GetIntegral(); integral != nil {
		att.MediaKey = integral.GetMediaKey()
		att.MediaSHA256 = integral.GetFileSHA256()
		att.MediaEncSHA256 = integral.GetFileEncSHA256()
		att.DirectPath = integral.GetDirectPath()
	}
}

func (c *Client) loggerSpecialMedia(message any) (*Attachment, string, bool) {
	if ca, ok := message.(*waConsumerApplication.ConsumerApplication); ok {
		content := ca.GetPayload().GetContent().GetViewOnceMessage()
		if content == nil {
			return nil, "", false
		}
		var att *Attachment
		var caption string
		switch inner := content.GetViewOnceContent().(type) {
		case *waConsumerApplication.ConsumerApplication_ViewOnceMessage_ImageMessage:
			att = c.extractE2EEImageAttachment(inner.ImageMessage)
			caption = inner.ImageMessage.GetCaption().GetText()
		case *waConsumerApplication.ConsumerApplication_ViewOnceMessage_VideoMessage:
			att = c.extractE2EEVideoAttachment(inner.VideoMessage)
			caption = inner.VideoMessage.GetCaption().GetText()
		default:
			att = &Attachment{Type: "attachment", Description: "Unsupported view-once content"}
		}
		att.ViewOnce, att.ViewMode = true, "once"
		return att, caption, true
	}
	armadillo, ok := message.(*waArmadilloApplication.Armadillo)
	if !ok {
		return nil, "", false
	}
	content := armadillo.GetPayload().GetContent()
	var raven *waArmadilloApplication.Armadillo_Content_RavenMessage
	switch wrapper := content.GetContent().(type) {
	case *waArmadilloApplication.Armadillo_Content_RavenMessageMsgr:
		raven = wrapper.RavenMessageMsgr
	case *waArmadilloApplication.Armadillo_Content_RavenMessage_:
		raven = wrapper.RavenMessage
	default:
		return nil, "", false
	}
	att := &Attachment{Type: "attachment"}
	if raven == nil {
		att.Description = "View-once media metadata unavailable"
		return att, "", true
	}
	switch raven.GetEphemeralType() {
	case waArmadilloApplication.Armadillo_Content_RavenMessage_VIEW_ONCE:
		att.ViewOnce, att.ViewMode = true, "once"
	case waArmadilloApplication.Armadillo_Content_RavenMessage_ALLOW_REPLAY:
		att.ViewOnce, att.ViewMode = true, "replay"
	default:
		att.ViewMode = "keep"
	}
	switch raven.GetMediaContent().(type) {
	case *waArmadilloApplication.Armadillo_Content_RavenMessage_ImageMessage:
		att.Type = "image"
		image, err := raven.DecodeImageMessage()
		if err != nil || image == nil {
			att.Description = "View-once image metadata could not be decoded"
			return att, "", true
		}
		att.Width, att.Height = int(image.GetAncillary().GetWidth()), int(image.GetAncillary().GetHeight())
		loggerTransport(att, image.GetIntegral().GetTransport())
	case *waArmadilloApplication.Armadillo_Content_RavenMessage_VideoMessage:
		att.Type = "video"
		video, err := raven.DecodeVideoMessage()
		if err != nil || video == nil {
			att.Description = "View-once video metadata could not be decoded"
			return att, "", true
		}
		att.Width, att.Height = int(video.GetAncillary().GetWidth()), int(video.GetAncillary().GetHeight())
		att.Duration = int(video.GetAncillary().GetSeconds())
		loggerTransport(att, video.GetIntegral().GetTransport())
	default:
		att.Description = "Unsupported view-once media type"
	}
	return att, "", true
}

// Initial bootstrap contacts are metadata, not old chat events. Previously the
// standalone adapter discarded this table and often retained only a push name.
func (c *Client) emitLoggerContacts(tbl *table.LSTable) {
	if tbl == nil {
		return
	}
	contacts := make([]map[string]any, 0)
	for _, row := range tbl.LSDeleteThenInsertContact {
		if row == nil {
			continue
		}
		username := row.Username
		if username == "" {
			username = row.SecondaryName
		}
		contacts = append(contacts, map[string]any{"id": row.Id, "name": row.Name, "firstName": row.FirstName, "username": username})
	}
	for _, row := range tbl.LSVerifyContactRowExists {
		if row == nil {
			continue
		}
		contacts = append(contacts, map[string]any{"id": row.ContactId, "name": row.Name, "firstName": row.FirstName, "username": row.SecondaryName})
	}
	threads := make([]map[string]any, 0)
	for _, row := range tbl.LSDeleteThenInsertThread {
		if row == nil {
			continue
		}
		threads = append(threads, map[string]any{"id": row.ThreadKey, "name": row.ThreadName, "type": row.ThreadType})
	}
	if len(contacts) > 0 || len(threads) > 0 {
		c.emitEvent(EventType("metadata"), map[string]any{"contacts": contacts, "threads": threads})
	}
}

func loggerContact(tbl *table.LSTable, userID int64) *ContactInfo {
	if tbl == nil {
		return nil
	}
	for _, row := range tbl.LSDeleteThenInsertContact {
		if row != nil && row.Id == userID {
			name, username := row.Name, row.Username
			if name == "" {
				name = row.FirstName
			}
			if username == "" {
				username = row.SecondaryName
			}
			return &ContactInfo{ID: userID, Name: name, Username: username}
		}
	}
	for _, row := range tbl.LSVerifyContactRowExists {
		if row != nil && row.ContactId == userID {
			name := row.Name
			if name == "" {
				name = row.FirstName
			}
			return &ContactInfo{ID: userID, Name: name, Username: row.SecondaryName}
		}
	}
	return nil
}

// A bounded, read-only lookup of a participant already observed in a new event.
func (c *Client) LoggerContact(userID int64) (*ContactInfo, error) {
	if userID <= 0 {
		return nil, fmt.Errorf("invalid contact ID")
	}
	ctx, cancel := context.WithTimeout(c.ctx, 10*time.Second)
	defer cancel()
	tbl, err := c.Messagix.ExecuteTasks(ctx, &socket.GetContactsFullTask{ContactID: userID})
	if err != nil {
		return nil, err
	}
	if contact := loggerContact(tbl, userID); contact != nil {
		return contact, nil
	}
	return nil, fmt.Errorf("contact name unavailable")
}
