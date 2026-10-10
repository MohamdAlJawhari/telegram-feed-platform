(() => {
    const avatars = [...document.querySelectorAll("[data-avatar-channel]")];
    function showPhoto(image, loaded) {
        image.hidden = !loaded;
        image.parentElement.querySelector(".avatar-initial").hidden = loaded;
    }
    document.addEventListener("load", event => {
        if (event.target.matches?.(".channel-profile-photo")) showPhoto(event.target, true);
    }, true);
    document.addEventListener("error", event => {
        if (event.target.matches?.(".channel-profile-photo")) showPhoto(event.target, false);
    }, true);
    avatars.forEach(avatar => {
        const image = avatar.querySelector("img");
        if (image && image.complete) showPhoto(image, image.naturalWidth > 0);
    });
    function update(channel) {
        if (!("avatar_url" in channel)) return;
        avatars.filter(avatar => avatar.dataset.avatarChannel === String(channel.channel_id)).forEach(avatar => {
            let image = avatar.querySelector("img");
            if (!channel.avatar_url) {
                image?.remove();
                avatar.querySelector(".avatar-initial").hidden = false;
                return;
            }
            if (image?.getAttribute("src") === channel.avatar_url) return;
            if (!image) {
                image = document.createElement("img");
                image.className = "channel-profile-photo";
                image.alt = "";
                image.decoding = "async";
                avatar.append(image);
            }
            showPhoto(image, false);
            image.src = channel.avatar_url;
        });
    }
    window.ChannelAvatars = { update };
})();
