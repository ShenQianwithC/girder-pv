import FrontPageView from 'girder/views/body/FrontPageView';
import { renderMarkdown } from 'girder/misc';
import { restRequest, getApiRoot } from 'girder/rest';
import { wrap } from 'girder/utilities/PluginUtils';

wrap(FrontPageView, 'render', function (render) {
    restRequest({
        method: 'GET',
        url: 'homepage'
    }).done((resp) => {
        if (resp['homepage.markdown']) {
            render.call(this);
               // 编辑首页默认显示的内容
//             if (resp['homepage.header']) {
//                 this.$('.g-frontpage-title').text(resp['homepage.header']);
//             }
//             if (resp['homepage.subheader']) {
//                 this.$('.g-frontpage-subtitle').text(resp['homepage.subheader']);
//             }
//             if (resp['homepage.welcome_text']) {
//                 this.$('.g-frontpage-welcome-text-content').html(renderMarkdown(resp['homepage.welcome_text']));
//             }
//             if (resp['homepage.logo']) {
//                 const logoUrl = `${getApiRoot()}/file/${resp['homepage.logo']}/download?contentDisposition=inline`;
//                 this.$('.g-frontpage-logo').attr('src', logoUrl);
//             }
        } else {
               // 显示setting DB取得homepage.markdown字段的正文内容
//             this.$el.html(renderMarkdown(resp['homepage.markdown']));
            this.$el.html(renderMarkdown("博思微视，世界一"));
        }
    });

    return this;
});
